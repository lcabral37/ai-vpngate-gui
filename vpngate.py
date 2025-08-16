import tkinter as tk
from tkinter import ttk, messagebox
import pandas as pd
import requests
import base64
import tempfile
import subprocess
import os
import json
from io import StringIO
from threading import Thread
import pystray
from PIL import Image, ImageDraw
import time


VPNGATE_API_URL = "https://www.vpngate.net/api/iphone/"
FAVORITES_FILE = "favorites.json"

class VPNGateApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VPNGate GUI Client")

        self.dataframe = pd.DataFrame()
        self.filtered_df = pd.DataFrame()
        self.vpn_process = None

        self.create_widgets()
        self.fetch_and_display()
        self.last_vpn_config = None
        self.auto_reconnect_enabled = True
        Thread(target=self.monitor_vpn_process, daemon=True).start()
        Thread(target=self.setup_tray_icon, daemon=True).start()

    def monitor_vpn_process(self):
        while True:
            time.sleep(5)
            if self.auto_reconnect_enabled and self.last_vpn_config:
                if self.vpn_process and self.vpn_process.poll() is not None:
                    print("VPN disconnected. Attempting to reconnect...")
                    self.start_vpn(self.last_vpn_config)

    def setup_tray_icon(self):
        # Create a simple icon
        icon_image = Image.new("RGB", (64, 64), color="blue")
        d = ImageDraw.Draw(icon_image)
        d.rectangle((10, 10, 54, 54), fill="white")

        menu = pystray.Menu(
            pystray.MenuItem("Reconnect", self.tray_reconnect),
            pystray.MenuItem("Disconnect", self.tray_disconnect),
            pystray.MenuItem("Exit", self.tray_exit)
        )

        self.tray_icon = pystray.Icon("VPNGate", icon_image, "VPNGate Client", menu)
        self.tray_icon.run()

    def tray_reconnect(self):
        if self.last_vpn_config:
            self.start_vpn(self.last_vpn_config)

    def tray_disconnect(self):
        self.disconnect_vpn()

    def tray_exit(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.quit()


    def create_widgets(self):
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=10)

        # Country dropdown
        tk.Label(control_frame, text="Country:").pack(side=tk.LEFT)
        self.country_var = tk.StringVar()
        self.country_dropdown = ttk.Combobox(control_frame, textvariable=self.country_var)
        self.country_dropdown.pack(side=tk.LEFT, padx=5)

        # Sort menu
        tk.Label(control_frame, text="Sort by:").pack(side=tk.LEFT)
        self.sort_var = tk.StringVar(value="Score")
        sort_menu = ttk.Combobox(control_frame, textvariable=self.sort_var, values=["Score", "Ping", "Speed"])
        sort_menu.pack(side=tk.LEFT, padx=5)

        # Buttons
        tk.Button(control_frame, text="Refresh", command=self.fetch_and_display).pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="Show Favorites", command=self.show_favorites).pack(side=tk.LEFT, padx=5)

        # Treeview
        self.tree = ttk.Treeview(self.root, columns=("Country", "IP", "Ping", "Speed", "Score"), show='headings')
        for col in self.tree["columns"]:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor=tk.CENTER, width=110)
        self.tree.pack(expand=True, fill="both", padx=10, pady=10)

        # Bottom buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Connect", command=self.connect_selected).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Disconnect", command=self.disconnect_vpn).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Add to Favorites ❤️", command=self.add_to_favorites).pack(side=tk.LEFT, padx=5)

        # --- VPN Log Output ---
        log_label = tk.Label(self.root, text="OpenVPN Logs:")
        log_label.pack(anchor="w", padx=10)

        self.log_text = tk.Text(self.root, height=10, state="disabled", bg="black", fg="lime", font=("Courier", 9))
        self.log_text.pack(fill="both", padx=10, pady=(0, 10), expand=False)

    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message)
        self.log_text.see("end")  # Auto-scroll
        self.log_text.configure(state="disabled")


    def fetch_and_display(self):
        try:
            response = requests.get(VPNGATE_API_URL)
            csv_data = response.text.split("#")[1].strip()
            df = pd.read_csv(StringIO(csv_data))
            df.dropna(subset=["OpenVPN_ConfigData_Base64"], inplace=True)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to fetch or parse VPN list:\n{e}")
            return

        # Convert speed to Mbps
        df["Speed"] = df["Speed"].apply(lambda x: self.human_readable_speed(x))
        self.dataframe = df
        self.populate_country_dropdown()
        self.update_table()

    def populate_country_dropdown(self):
        countries = sorted(self.dataframe["CountryLong"].dropna().unique())
        self.country_dropdown["values"] = ["All"] + countries
        self.country_dropdown.current(0)

    def human_readable_speed(self, bps):
        try:
            bps = float(bps)
            if bps >= 1e9:
                return f"{bps/1e9:.2f} Gbps"
            elif bps >= 1e6:
                return f"{bps/1e6:.2f} Mbps"
            elif bps >= 1e3:
                return f"{bps/1e3:.2f} Kbps"
            else:
                return f"{bps:.0f} bps"
        except:
            return "N/A"

    def update_table(self, df=None):
        if df is None:
            df = self.dataframe

        country = self.country_var.get()
        if country and country != "All":
            df = df[df["CountryLong"] == country]

        sort_col = self.sort_var.get()
        if sort_col in df.columns:
            df = df.sort_values(by=sort_col, ascending=False)

        self.filtered_df = df.reset_index(drop=True)

        # Clear and update tree
        for row in self.tree.get_children():
            self.tree.delete(row)

        for i, row in self.filtered_df.iterrows():
            self.tree.insert('', tk.END, values=(row["CountryLong"], row["IP"], row["Ping"], row["Speed"], row["Score"]))

    def connect_selected(self):
        selected_item = self.tree.focus()
        if not selected_item:
            messagebox.showwarning("Warning", "No VPN selected.")
            return

        index = self.tree.index(selected_item)
        row = self.filtered_df.iloc[index]
        config_b64 = row["OpenVPN_ConfigData_Base64"]

        try:
            config_data = base64.b64decode(config_b64)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ovpn") as temp_file:
                temp_file.write(config_data)
                temp_file_path = temp_file.name

            self.last_vpn_config = temp_file_path
            confirm = messagebox.askyesno("Connect", f"Connect to {row['CountryLong']} ({row['IP']})?")
            if confirm:
                self.start_vpn(temp_file_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect:\n{e}")

    def start_vpn(self, config_path):
        if self.vpn_process and self.vpn_process.poll() is None:
            self.vpn_process.terminate()
            time.sleep(2)

        try:
            self.log_text.configure(state="normal")
            self.log_text.delete(1.0, "end")
            self.log_text.configure(state="disabled")

            self.vpn_process = subprocess.Popen(
                ["sudo", "openvpn", "--config", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True
            )

            Thread(target=self.read_vpn_output, daemon=True).start()
        except Exception as e:
            self.append_log(f"[ERROR] VPN start failed: {e}\n")

    def read_vpn_output(self):
        if not self.vpn_process:
            return

        for line in self.vpn_process.stdout:
            self.append_log(line)

    def disconnect_vpn(self):
        if self.vpn_process and self.vpn_process.poll() is None:
            self.vpn_process.terminate()
            self.vpn_process = None
            messagebox.showinfo("Disconnected", "OpenVPN process terminated.")
        else:
            messagebox.showinfo("Info", "No active VPN connection.")

    def add_to_favorites(self):
        selected_item = self.tree.focus()
        if not selected_item:
            messagebox.showwarning("Warning", "No VPN selected.")
            return

        index = self.tree.index(selected_item)
        row = self.filtered_df.iloc[index].to_dict()

        favorites = self.load_favorites()
        favorites.append(row)
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(favorites, f, indent=2)

        messagebox.showinfo("Added", "VPN added to favorites.")

    def load_favorites(self):
        if not os.path.exists(FAVORITES_FILE):
            return []
        with open(FAVORITES_FILE, 'r') as f:
            return json.load(f)

    def show_favorites(self):
        try:
            favorites = pd.DataFrame(self.load_favorites())
            if favorites.empty:
                messagebox.showinfo("Favorites", "No favorites saved.")
                return
            favorites["Speed"] = favorites["Speed"].apply(self.human_readable_speed)
            self.update_table(favorites)
        except Exception as e:
            messagebox.showerror("Error", f"Could not load favorites:\n{e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = VPNGateApp(root)
    root.mainloop()
