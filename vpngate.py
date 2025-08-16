#!/usr/bin/env python
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
import psutil
import signal
import atexit



VPNGATE_API_URL = "https://www.vpngate.net/api/iphone/"
FAVORITES_FILE = "favorites.json"
CACHE_FILE = "vpnlist_cache.csv"

class VPNGateApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VPNGate GUI Client")

        self.own_vpn_pids = set()

        self.favorites = self.load_favorites()  # a list of dicts
        self.favorite_ips = {vpn['IP'] for vpn in self.favorites}

        self.dataframe = pd.DataFrame()
        self.filtered_df = pd.DataFrame()
        self.vpn_process = None

        self.create_widgets()
        self.load_cached_data()

        self.last_vpn_config = None
        self.auto_reconnect_enabled = True
        self.log_file_path = "vpn_logs.log"

        Thread(target=self.monitor_vpn_process, daemon=True).start()
        Thread(target=self.setup_tray_icon, daemon=True).start()
        self.check_openvpn_process_count()

        atexit.register(self.cleanup_on_exit)
        signal.signal(signal.SIGINT, lambda sig, frame: self.cleanup_and_quit())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.cleanup_and_quit())


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
        self.country_dropdown.pack(side=tk.LEFT, padx=5)
        self.country_dropdown.bind("<<ComboboxSelected>>", lambda e: self.update_table())

        # # Sort menu
        tk.Label(control_frame, text="Sort by:").pack(side=tk.LEFT)
        self.sort_var = tk.StringVar(value="Score")
        sort_menu = ttk.Combobox(control_frame, textvariable=self.sort_var, values=["Score", "Ping", "Speed", "NumVpnSessions"])
        sort_menu.pack(side=tk.LEFT, padx=5)

        # Buttons
        tk.Button(control_frame, text="Reload List", command=self.fetch_and_display).pack(side=tk.LEFT, padx=5)
        #        tk.Button(control_frame, text="Show Favorites", command=self.show_favorites).pack(side=tk.LEFT, padx=5)



        # location
        self.location_label = tk.Label(control_frame, text="Location: Unknown")
        self.location_label.pack(side=tk.LEFT, padx=10)

        tk.Button(control_frame, text="Check Location", command=self.check_location).pack(side=tk.LEFT, padx=5)

        # Treeview
        self.tree = ttk.Treeview(self.root, columns=("Favorite", "Country", "IP", "Ping", "Speed", "Score", "NumVpnSessions"), show='headings')
        self.sort_direction = {}  # Store sort direction per column
        for col in self.tree["columns"]:
            self.tree.heading(col, text=col, command=lambda _col=col: self.sort_by_column(_col) if _col != "Favorite" else None)
            self.tree.column(col, anchor=tk.CENTER, width=100)

        self.tree.pack(expand=True, fill="both", padx=10, pady=10)

        # Bottom buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Connect", command=self.connect_selected).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Disconnect", command=self.disconnect_vpn).pack(side=tk.LEFT, padx=5)
        tk.Button(btn_frame, text="Kill All OpenVPN 🔥", command=self.kill_all_openvpn).pack(side=tk.LEFT, padx=5)

        self.vpn_proc_label = tk.Label(btn_frame, text="OpenVPN Processes: 0")
        self.vpn_proc_label.pack(pady=5)

        # --- VPN Log Output ---
        log_label = tk.Label(self.root, text="OpenVPN Logs:")
        log_label.pack(anchor="w", padx=10)

        self.log_text = tk.Text(self.root, height=10, state="disabled", bg="black", fg="lime", font=("Courier", 9))
        self.log_text.pack(fill="both", padx=10, pady=(0, 10), expand=False)
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Add to Favorites ❤️", command=self.context_add_to_favorites)
        self.context_menu.add_command(label="Remove from Favorites ❌", command=self.context_remove_from_favorites)

        self.tree.bind("<Button-3>", self.show_context_menu)  # Right-click


    def check_location(self):
        try:
            response = requests.get("https://ipwho.is")
            data = response.json()

            if data.get("success"):
                country = data.get("country", "Unknown")
                city = data.get("city", "Unknown")
                ip = data.get("ip", "Unknown")
                isp = data.get("connection", {}).get("isp", "Unknown")
                self.location_label.config(text=f"Location: {city}, {country} ({ip})")
            else:
                self.location_label.config(text="Location: Unable to detect")
        except Exception as e:
            self.location_label.config(text="Location: Error")

    def kill_all_openvpn(self):
        killed = 0
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'openvpn' in proc.info['name'].lower() or 'openvpn' in ' '.join(proc.info['cmdline']).lower():
                    proc.kill()
                    killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            messagebox.showinfo("Success", f"Killed {killed} OpenVPN process(es).")
        else:
            messagebox.showinfo("Info", "No OpenVPN processes found.")

    def check_openvpn_process_count(self):
        count = 0
        for proc in psutil.process_iter(['name', 'cmdline']):
            try:
                if 'openvpn' in proc.info['name'].lower() or 'openvpn ' in ' '.join(proc.info['cmdline']).lower():
                    count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.vpn_proc_label.config(text=f"OpenVPN Processes: {count}")

        # Schedule to run again in 5 seconds (5000 milliseconds)
        self.root.after(5000, self.check_openvpn_process_count)


    def append_log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message)
        self.log_text.see("end")  # Auto-scroll
        self.log_text.configure(state="disabled")

    def refresh_data(self):
        try:
            response = requests.get(VPNGATE_API_URL)
            csv_data = response.text.split("#")[1].strip()
            df = pd.read_csv(StringIO(csv_data))
            df.dropna(subset=["OpenVPN_ConfigData_Base64"], inplace=True)

            # Save to cache
            df.to_csv(CACHE_FILE, index=False)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to fetch or parse VPN list:\n{e}")
            return

        # Convert speed
        df["Speed"] = df["Speed"].apply(lambda x: self.human_readable_speed(x))
        self.dataframe = df
        self.populate_country_dropdown()
        self.update_table()

    def load_cached_data(self):
        if not os.path.exists(CACHE_FILE):
            self.refresh_data()
            return

        try:
            df = pd.read_csv(CACHE_FILE)
            df["Speed"] = df["Speed"].apply(lambda x: self.human_readable_speed(x))
            self.dataframe = df
            self.populate_country_dropdown()
            self.update_table()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load cached data:\n{e}")
            self.refresh_data()

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
            fav_mark = "❤️" if row["IP"] in self.favorite_ips else ""
            self.tree.insert('', tk.END, values=(fav_mark, row["CountryLong"], row["IP"], row["Ping"], row["Speed"], row["Score"]))

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
                ["openvpn", "--data-ciphers", "AES-128-CBC", "--config", config_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True
            )

            Thread(target=self.read_vpn_output, daemon=True).start()
            self.vpn_process = subprocess.Popen([...])
            self.own_vpn_pids.add(self.vpn_process.pid)

            # ✅ Trigger location check after 5 seconds (5000 ms)
            self.root.after(5000, self.check_location)
        except Exception as e:
            self.append_log(f"[ERROR] VPN start failed: {e}\n")

    def read_vpn_output(self):
        if not self.vpn_process:
            return

        with open(self.log_file_path, "a") as log_file:
            for line in self.vpn_process.stdout:
                self.append_log(line)
                log_file.write(line)
                log_file.flush()
                print(line)

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
            return

        index = self.tree.index(selected_item)
        row = self.filtered_df.iloc[index].to_dict()

        favorites = self.load_favorites()
        favorites.append(row)
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(favorites, f, indent=2)

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

    def sort_by_column(self, col):
        if col not in self.filtered_df.columns:
            return

        # Toggle sort direction
        ascending = self.sort_direction.get(col, False)
        self.sort_direction[col] = not ascending

        self.filtered_df = self.filtered_df.sort_values(by=col, ascending=ascending).reset_index(drop=True)

        # Update treeview
        for row in self.tree.get_children():
            self.tree.delete(row)

        for _, row in self.filtered_df.iterrows():
            self.tree.insert('', tk.END, values=(row["CountryLong"], row["IP"], row["Ping"], row["Speed"], row["Score"], row["NumVpnSessions"]))

    def show_context_menu(self, event):
        selected_item = self.tree.identify_row(event.y)
        if selected_item:
            self.tree.selection_set(selected_item)
            self.context_menu.post(event.x_root, event.y_root)

    def context_add_to_favorites(self):
        selected_item = self.tree.focus()
        if not selected_item:
            return

        index = self.tree.index(selected_item)
        row = self.filtered_df.iloc[index].to_dict()
        if row["IP"] in self.favorite_ips:
            return  # Already a favorite

        self.favorites.append(row)
        self.favorite_ips.add(row["IP"])
        with open(FAVORITES_FILE, 'w') as f:
            json.dump(self.favorites, f, indent=2)
        self.update_table()

    def context_remove_from_favorites(self):
        selected_item = self.tree.focus()
        if not selected_item:
            return

        index = self.tree.index(selected_item)
        row = self.filtered_df.iloc[index].to_dict()

        self.favorites = [fav for fav in self.favorites if fav["IP"] != row["IP"]]
        self.favorite_ips.discard(row["IP"])

        with open(FAVORITES_FILE, 'w') as f:
            json.dump(self.favorites, f, indent=2)
        self.update_table()

    def cleanup_and_quit(self):
        self.cleanup_on_exit()
        self.root.quit()

    def cleanup_on_exit(self):
        print("Cleaning up OpenVPN processes...")
        for pid in list(self.own_vpn_pids):
            try:
                proc = psutil.Process(pid)
                if "openvpn" in proc.name().lower():
                    proc.terminate()
                    print(f"Terminated VPN process {pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Optionally: Kill ALL openvpn processes (if you're okay with it)
        for proc in psutil.process_iter(['name']):
            try:
                if 'openvpn' in proc.info['name'].lower():
                    proc.kill()
                    print(f"Force killed lingering OpenVPN process PID {proc.pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue


if __name__ == "__main__":
    root = tk.Tk()
    app = VPNGateApp(root)
    root.mainloop()
