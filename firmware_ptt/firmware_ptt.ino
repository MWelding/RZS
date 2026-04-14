/*
 * ================================================================
 *  PTT READER FIRMWARE — XIAO ESP32-C3
 *  YL3RZ Coax Switch Project
 *
 *  Reads PTT output pin from FTDX10 (or any radio) and broadcasts
 *  state to all connected WebSocket clients in real-time.
 *
 *  Protocol (WebSocket JSON, push-only from ESP):
 *    ESP → Client:  {"type":"hello","device":"ptt_reader","name":"..."}
 *    ESP → Client:  {"type":"ptt","active":true/false,"timestamp":N}
 *
 *  UDP Discovery (port 5000):
 *    Client sends:   COAX_DISCOVERY
 *    ESP responds:   COAX_DEVICE:DeviceName:IP:ptt_reader
 *
 *  Wiring (FTDX10 PTT Output → XIAO ESP32-C3):
 *    FTDX10 PTT Output (active LOW) → PTT_PIN (with 10kΩ pull-up to 3.3V)
 *    FTDX10 GND                     → ESP GND
 *    NOTE: FTDX10 PTT output is open-collector. No level translation needed
 *          as long as the radio and ESP share a common ground.
 * ================================================================
 */

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncTCP.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>

// ================= USER CONFIG (edit these) =================
const char* SSID          = "MTD1";
const char* PASSWORD      = "4IDLDLBEVW";
const char* DEVICE_NAME   = "PTT_Reader";

// PTT input pin. FTDX10 PTT output is open-collector, active LOW.
// Wire with a 10kΩ pull-up resistor to 3.3V.
const int   PTT_PIN       = 2;          // D0 on XIAO ESP32-C3

// Set to true  if PTT is active LOW (radio pulls pin to GND when TX)
// Set to false if PTT is active HIGH
const bool  PTT_ACTIVE_LOW = true;

// Debounce time in milliseconds
const int   DEBOUNCE_MS   = 5;

const int   DISCOVERY_PORT = 5000;
// ============================================================

AsyncWebServer  server(80);
AsyncWebSocket  ws("/ws");
WiFiUDP         udp;

volatile bool pttRaw     = false;   // Raw GPIO reading
bool          pttState   = false;   // Debounced state (last broadcast)
unsigned long lastChange = 0;       // Last GPIO change timestamp

// ---- Build JSON ----
String buildHelloJSON() {
    String s = "{\"type\":\"hello\",\"device\":\"ptt_reader\",\"name\":\"";
    s += DEVICE_NAME;
    s += "\",\"version\":\"2.0\"}";
    return s;
}

String buildPTTJSON(bool active) {
    String s = "{\"type\":\"ptt\",\"active\":";
    s += active ? "true" : "false";
    s += ",\"timestamp\":";
    s += millis();
    s += "}";
    return s;
}

// Removed ISR - using pure software polling to prevent WDT crashes from wire bouncing
bool          lastReading = false;

// ---- WebSocket ----
void onWsEvent(AsyncWebSocket *server, AsyncWebSocketClient *client,
               AwsEventType type, void *arg, uint8_t *data, size_t len) {
    if (type == WS_EVT_CONNECT) {
        // Send hello + current PTT state on connect
        client->text(buildHelloJSON());
        client->text(buildPTTJSON(pttState));
        Serial.println("WS Client connected: " + String(client->id()));
    } else if (type == WS_EVT_DISCONNECT) {
        Serial.println("WS Client disconnected: " + String(client->id()));
    }
    // Note: PTT reader is push-only, no commands accepted
}

// ---- UDP Discovery ----
void handleDiscovery() {
    int sz = udp.parsePacket();
    if (!sz) return;
    char buf[32];
    int len = udp.read(buf, 31);
    if (len <= 0) return;
    buf[len] = 0;
    if (strcmp(buf, "COAX_DISCOVERY") == 0) {
        String resp = "COAX_DEVICE:" + String(DEVICE_NAME) + ":" + WiFi.localIP().toString() + ":ptt_reader";
        udp.beginPacket(udp.remoteIP(), udp.remotePort());
        udp.print(resp);
        udp.endPacket();
        Serial.println("Discovery: responded to " + udp.remoteIP().toString());
    }
}

// ---- WiFi ----
void connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(SSID, PASSWORD);
    Serial.print("Connecting to WiFi");
    unsigned long t = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t < 15000) {
        delay(300); Serial.print(".");
    }
    if (WiFi.status() != WL_CONNECTED) { Serial.println("\nFailed, restarting."); ESP.restart(); }
    Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
}

void setup() {
    Serial.begin(115200);

    // PTT pin: input with internal pull-up if active-low
    if (PTT_ACTIVE_LOW) {
        pinMode(PTT_PIN, INPUT_PULLUP);
    } else {
        pinMode(PTT_PIN, INPUT);
    }

    pttRaw   = digitalRead(PTT_PIN);
    // If active low, invert: pin LOW = PTT active
    pttState = PTT_ACTIVE_LOW ? (pttRaw == LOW) : (pttRaw == HIGH);
    lastReading = pttState;

    // Interrupt removed for bench testing stability - loop() will poll at ~1MHz natively

    connectWiFi();

    if (MDNS.begin("esp32-ptt")) {
        MDNS.addService("http", "tcp", 80);
        MDNS.addService("ws",   "tcp", 80);
    }

    udp.begin(DISCOVERY_PORT);

    ws.onEvent(onWsEvent);
    server.addHandler(&ws);
    server.begin();

    Serial.println("PTT Reader ready. WS at ws://" + WiFi.localIP().toString() + "/ws");
}

void loop() {
    // Robust software debounce algorithm
    bool reading = digitalRead(PTT_PIN);
    bool activeNow = PTT_ACTIVE_LOW ? (reading == LOW) : (reading == HIGH);

    if (activeNow != lastReading) {
        lastChange = millis();
    }

    if ((millis() - lastChange) >= DEBOUNCE_MS) {
        if (activeNow != pttState) {
            pttState = activeNow;
            String msg = buildPTTJSON(pttState);
            ws.textAll(msg);
            Serial.println("PTT: " + String(pttState ? "ACTIVE (TX)" : "INACTIVE (RX)"));
        }
    }
    lastReading = activeNow;

    handleDiscovery();
    ws.cleanupClients();
    yield();
}
