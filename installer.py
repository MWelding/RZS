import os
import sys
import shutil
import tkinter as tk
from tkinter import messagebox

def get_resource(name):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, name)

def create_shortcut(target, name, icon):
    try:
        import win32com.client
        desktop = os.path.join(os.environ['USERPROFILE'], 'Desktop')
        path = os.path.join(desktop, f"{name}.lnk")
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(path)
        shortcut.Targetpath = target
        shortcut.WorkingDirectory = os.path.dirname(target)
        shortcut.IconLocation = icon
        shortcut.save()
        return True
    except Exception as e:
        print("Shortcut failed:", e)
        return False

def install():
    install_btn.config(state="disabled", text="Installing...")
    root.update()
    
    install_dir = os.path.join(os.environ['USERPROFILE'], "AntennaMini")
    try:
        os.makedirs(install_dir, exist_ok=True)
        
        # Move files
        shutil.copy2(get_resource("main.exe"), os.path.join(install_dir, "AntennaMini.exe"))
        
        # Important: Never overwrite user config if they are just reinstalling/updating
        tgt_config = os.path.join(install_dir, "config.xml")
        if not os.path.exists(tgt_config):
            shutil.copy2(get_resource("config.xml"), tgt_config)
            
        shutil.copy2(get_resource("instructions.txt"), os.path.join(install_dir, "instructions.txt"))
        
        # Create Desktop Shortcut
        target_exe = os.path.join(install_dir, "AntennaMini.exe")
        create_shortcut(target_exe, "Antenna Mini", target_exe)
        
        messagebox.showinfo("Success", f"Antenna Mini has been gracefully installed to:\n{install_dir}\n\nA shortcut was added to your desktop!")
        root.destroy()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to install: {e}")
        install_btn.config(state="normal", text="Install Now")

root = tk.Tk()
root.title("Antenna Mini Installer")
root.geometry("340x160")

try:
    icon_path = get_resource("logo1.ico")
    if os.path.exists(icon_path):
        root.iconbitmap(icon_path)
except:
    pass
    
# Center window
root.eval('tk::PlaceWindow . center')

tk.Label(root, text="Antenna Mini Setup", font=("Segoe UI", 12, "bold")).pack(pady=(15, 5))
tk.Label(root, text="This wizard will install the application and\ncreate a desktop shortcut.", font=("Segoe UI", 9)).pack(pady=(0, 15))

install_btn = tk.Button(root, text="Install Now", command=install, width=18, bg="#0078D7", fg="white", font=("Segoe UI", 10, "bold"), relief="flat", cursor="hand2")
install_btn.pack()

root.mainloop()
