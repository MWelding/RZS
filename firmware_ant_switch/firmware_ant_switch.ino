/*
 * ================================================================
 *  ANTENNA SWITCH FIRMWARE — XIAO ESP32-C3 + ULN2003A
 *  YL3RZ Coax Switch Project
 *  
 *  Protocol (WebSocket JSON bidirectional):
 *    Client → ESP:  {"cmd":"set","relay":N,"state":true/false}
 *    Client → ESP:  {"cmd":"status"}
 *    ESP → Client:  {"type":"state","relays":[bool,...],"ptt":false,"ptt_safe":true}
 *    ESP → Client:  {"type":"hello","device":"ant_switch","version":"2.0","relays":5}
 *    
 *  UDP Discovery (port 5000):
 *    Client sends:   COAX_DISCOVERY
 *    ESP responds:   COAX_DEVICE:DeviceName:IP:ant_switch
 * ================================================================
 */

#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncTCP.h>
#include <WiFiUdp.h>
#include <time.h>
#include <ESPmDNS.h>

// ================= USER CONFIG (edit these) =================
const char* SSID         = "MTD1";
const char* PASSWORD     = "4IDLDLBEVW";
const char* DEVICE_NAME  = "AntSwitch";  // Change per device

// XIAO ESP32-C3 Pins → ULN2003A inputs
// Xiao: D0=2, D1=3, D2=4, D3=5, D4=6  (up to 5 relays default)
const int RELAY_PINS[]   = { 2, 3, 4, 5, 6 };
const int NUM_RELAYS     = sizeof(RELAY_PINS) / sizeof(RELAY_PINS[0]);

const int  DISCOVERY_PORT = 5000;
// ============================================================

AsyncWebServer  server(80);
AsyncWebSocket  ws("/ws");
WiFiUDP         udp;

bool relayState[10] = {false};

// ---- Relay control ----
void setRelay(int idx, bool state) {
    if (idx < 0 || idx >= NUM_RELAYS) return;
    if (relayState[idx] == state) return;
    digitalWrite(RELAY_PINS[idx], state ? HIGH : LOW);
    relayState[idx] = state;
    broadcastState();
}

String buildStateJSON() {
    String s = "{\"type\":\"state\",\"relays\":[";
    for (int i = 0; i < NUM_RELAYS; i++) {
        s += relayState[i] ? "true" : "false";
        if (i < NUM_RELAYS - 1) s += ",";
    }
    s += "],\"ptt\":false,\"ptt_safe\":true}";
    return s;
}

String buildHelloJSON() {
    String s = "{\"type\":\"hello\",\"device\":\"ant_switch\",\"name\":\"";
    s += DEVICE_NAME;
    s += "\",\"version\":\"2.0\",\"relays\":";
    s += NUM_RELAYS;
    s += "}";
    return s;
}

void broadcastState() {
    String msg = buildStateJSON();
    ws.textAll(msg);
}

// ---- WebSocket event handler ----
void onWsEvent(AsyncWebSocket *server, AsyncWebSocketClient *client,
               AwsEventType type, void *arg, uint8_t *data, size_t len) {
    if (type == WS_EVT_CONNECT) {
        // Send hello + current state on connect
        client->text(buildHelloJSON());
        client->text(buildStateJSON());
        Serial.println("WS Client connected: " + String(client->id()));

    } else if (type == WS_EVT_DISCONNECT) {
        Serial.println("WS Client disconnected: " + String(client->id()));

    } else if (type == WS_EVT_DATA) {
        AwsFrameInfo *info = (AwsFrameInfo*)arg;
        if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT) {
            String msg = String((char*)data, len);
            // Parse {"cmd":"set","relay":N,"state":bool}
            int cmdIdx = msg.indexOf("\"cmd\":\"set\"");
            int relayIdx_pos = msg.indexOf("\"relay\":");
            int stateIdx = msg.indexOf("\"state\":");

            if (cmdIdx >= 0 && relayIdx_pos >= 0 && stateIdx >= 0) {
                int rStart = relayIdx_pos + 8;
                int rEnd   = msg.indexOf(",", rStart);
                if (rEnd < 0) rEnd = msg.indexOf("}", rStart);
                int relayNum = msg.substring(rStart, rEnd).toInt();

                bool newState = false;
                int sStart = stateIdx + 8;
                if (msg.indexOf("true", sStart) == sStart) newState = true;

                setRelay(relayNum, newState);
                Serial.printf("WS CMD: relay=%d state=%s\n", relayNum, newState ? "ON" : "OFF");

            } else if (msg.indexOf("\"cmd\":\"status\"") >= 0) {
                client->text(buildStateJSON());
            }
        }
    }
}

// ---- HTTP fallback (for legacy compatibility) ----
void handleHttpStatus(AsyncWebServerRequest *request) {
    request->send(200, "application/json", buildStateJSON());
}

void handleHttpSet(AsyncWebServerRequest *request) {
    if (!request->hasParam("plain", true)) {
        request->send(400, "application/json", "{\"success\":false}");
        return;
    }
    String body = request->getParam("plain", true)->value();
    int relayNum = -1;
    bool newState = false;

    int ri = body.indexOf("\"relay\":");
    if (ri >= 0) {
        int s = ri + 8, e = body.indexOf(",", s);
        if (e < 0) e = body.indexOf("}", s);
        relayNum = body.substring(s, e).toInt();
    }
    int si = body.indexOf("\"state\":");
    if (si >= 0) newState = (body.indexOf("true", si + 8) == si + 8);

    if (relayNum >= 0 && relayNum < NUM_RELAYS) {
        setRelay(relayNum, newState);
        request->send(200, "application/json", "{\"success\":true}");
    } else {
        request->send(400, "application/json", "{\"success\":false,\"reason\":\"invalid_relay\"}");
    }
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
        String resp = "COAX_DEVICE:" + String(DEVICE_NAME) + ":" + WiFi.localIP().toString() + ":ant_switch";
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
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\nFailed, restarting...");
        ESP.restart();
    }
    Serial.println("\nConnected! IP: " + WiFi.localIP().toString());
}

void setup() {
    Serial.begin(115200);

    for (int i = 0; i < NUM_RELAYS; i++) {
        pinMode(RELAY_PINS[i], OUTPUT);
        digitalWrite(RELAY_PINS[i], LOW);
        relayState[i] = false;
    }

    connectWiFi();

    if (MDNS.begin("esp32-antswitch")) {
        MDNS.addService("http", "tcp", 80);
        MDNS.addService("ws",   "tcp", 80);
    }

    udp.begin(DISCOVERY_PORT);
    configTime(0, 0, "pool.ntp.org");

    // WebSocket
    ws.onEvent(onWsEvent);
    server.addHandler(&ws);

    // HTTP endpoints
    server.on("/status", HTTP_GET,     handleHttpStatus);
    server.on("/set",    HTTP_POST,    handleHttpSet);
    server.on("/set",    HTTP_OPTIONS, [](AsyncWebServerRequest *r){
        AsyncWebServerResponse *resp = r->beginResponse(204);
        resp->addHeader("Access-Control-Allow-Origin", "*");
        resp->addHeader("Access-Control-Allow-Methods", "POST,OPTIONS");
        resp->addHeader("Access-Control-Allow-Headers", "*");
        r->send(resp);
    });

    server.begin();
    Serial.println("Server started. WS at ws://" + WiFi.localIP().toString() + "/ws");
}

void loop() {
    handleDiscovery();
    ws.cleanupClients();  // Remove stale connections
    yield();
}
