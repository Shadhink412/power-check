#!/usr/bin/env python3
"""
bot.py

Power / Charging monitor with Telegram bot interface.

Features:
- Detect platform (Windows, Linux, Android/Termux).
- Interactive setup on first run to collect Telegram bot token and mode (admin-only or multi-user).
- Save configuration and runtime state in `data.json`.
- Monitor battery/charging status and notify registered users on transitions.
- Commands: /start, /status, /unregister, /help, /reconfigure (admin only).
- Uses psutil when possible and falls back to sysfs or termux commands.

Usage:
    1. Install dependencies: pip install -r requirements.txt
    2. Run: python bot.py
    3. For persistent running: configure systemd (Linux) or Task Scheduler (Windows).

Note:
- Keep the `data.json` secure because it contains the bot token.
"""

import os
import sys
import json
import time
import threading
import platform
import getpass
import subprocess
from typing import Optional, Dict, Any, List, Tuple

try:
    import psutil
except Exception:
    psutil = None  # will attempt fallback methods

try:
    import telebot  # pyTelegramBotAPI
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
except Exception:
    print("ERROR: telebot (pyTelegramBotAPI) is required. Install from requirements.txt")
    raise

DATA_FILE = "data.json"
TEMP_DATA_FILE = "data.json.tmp"
DEFAULT_POLL_INTERVAL = 5  # seconds


# ----------------------------
# Utilities: safe json read/write
# ----------------------------
def safe_read_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to read {path}: {e}")
        return None


def safe_write_json(path: str, data: dict) -> bool:
    try:
        with open(TEMP_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(TEMP_DATA_FILE, path)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to write {path}: {e}")
        return False


# ----------------------------
# Platform / device detection
# ----------------------------
def detect_platform() -> str:
    plat = platform.system().lower()
    if "linux" in plat:
        # check for Android (Termux) heuristics
        if "android" in platform.release().lower() or os.path.exists("/system/bin/termux-am"):
            return "android"
        # additional check: presence of 'com.termux' or android-specific dirs
        if os.path.exists("/data/data/com.termux"):
            return "android"
        return "linux"
    if "windows" in plat:
        return "windows"
    if "darwin" in plat:
        return "mac"
    return plat


# ----------------------------
# Battery reading helpers
# ----------------------------
def read_battery_psutil() -> Optional[Dict[str, Any]]:
    if psutil is None:
        return None
    try:
        b = psutil.sensors_battery()
        if b is None:
            return None
        return {
            "percent": float(b.percent) if b.percent is not None else None,
            "power_plugged": bool(b.power_plugged),
            "secsleft": int(b.secsleft) if b.secsleft is not None else None,
            "source": "psutil",
        }
    except Exception:
        return None


def read_battery_linux_sysfs() -> Optional[Dict[str, Any]]:
    """
    Try to parse /sys/class/power_supply/* for status and capacity.
    Returns None if unable to locate battery info.
    """
    base = "/sys/class/power_supply"
    if not os.path.isdir(base):
        return None
    candidates = []
    for name in os.listdir(base):
        path = os.path.join(base, name)
        # common battery names: BAT0, battery
        if os.path.isdir(path):
            candidates.append(path)
    best = None
    for cand in candidates:
        # attempt to read typical files
        status_file = os.path.join(cand, "status")
        capacity_file = os.path.join(cand, "capacity")
        if os.path.exists(status_file) and os.path.exists(capacity_file):
            try:
                with open(status_file, "r", encoding="utf-8") as f:
                    status = f.read().strip().lower()
                with open(capacity_file, "r", encoding="utf-8") as f:
                    cap = f.read().strip()
                percent = float(cap)
                plugged = status in ("charging", "full", "pending_charge")
                return {
                    "percent": percent,
                    "power_plugged": plugged,
                    "secsleft": None,
                    "source": f"sysfs:{os.path.basename(cand)}",
                }
            except Exception:
                continue
    return None


def read_battery_termux() -> Optional[Dict[str, Any]]:
    """
    Use `termux-battery-status` if available (Termux environment).
    It returns JSON, e.g. {"health":"good","percentage":95,"temperature":33.3,"plugged":false}
    """
    try:
        result = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=3)
        if result.returncode == 0 and result.stdout:
            data = json.loads(result.stdout)
            pct = float(data.get("percentage")) if data.get("percentage") is not None else None
            plugged = bool(data.get("plugged"))
            return {
                "percent": pct,
                "power_plugged": plugged,
                "secsleft": None,
                "source": "termux-battery-status",
            }
    except Exception:
        pass
    return None


def get_battery_snapshot(detected_platform: str) -> Optional[Dict[str, Any]]:
    """
    Return a dict: {percent: float|None, power_plugged: bool|None, secsleft: int|None, source: str}
    """
    # prefer psutil when available
    st = read_battery_psutil()
    if st:
        return st
    # platform-specific fallbacks
    if detected_platform == "linux":
        st = read_battery_linux_sysfs()
        if st:
            return st
    if detected_platform == "android":
        st = read_battery_termux()
        if st:
            return st
        # fall back to sysfs as well
        st = read_battery_linux_sysfs()
        if st:
            return st
    if detected_platform == "windows":
        # try psutil only (already tried). Windows fallback is uncommon in Python.
        return None
    # macOS support via psutil only
    return None


def format_time_left(secs: Optional[int]) -> str:
    if secs is None:
        return "unknown"
    if secs == psutil.POWER_TIME_UNLIMITED if psutil else -1:
        return "unlimited"
    if secs == psutil.POWER_TIME_UNKNOWN if psutil else -1:
        return "unknown"
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h}h {m}m"


def snapshot_text(snap: Optional[Dict[str, Any]]) -> str:
    if snap is None:
        return "Battery information not available."
    pct = snap.get("percent")
    plugged = snap.get("power_plugged")
    pct_str = f"{pct:.0f}%" if (pct is not None) else "n/a"
    
    if plugged:
        status = "Power ON"
    elif plugged is False:
        status = "Power OFF"
    else:
        status = "Unknown"
    
    return f"{status} - Battery: {pct_str}"


# ----------------------------
# Config / state handling
# ----------------------------
def default_state(platform_name: str) -> dict:
    return {
        "platform": platform_name,
        "bot_token": None,
        "mode": "multi",  # "admin" or "multi"
        "admin_ids": [],
        "registered_ids": [],
        "poll_interval": DEFAULT_POLL_INTERVAL,
    }


def setup_from_env(platform_name: str) -> Optional[dict]:
    """Try to setup configuration from environment variables"""
    token = os.environ.get("BOT_TOKEN")
    if not token:
        return None
    
    cfg = default_state(platform_name)
    cfg["bot_token"] = token
    
    # Mode setup
    mode = os.environ.get("BOT_MODE", "multi").lower()
    if mode in ["admin", "1"]:
        cfg["mode"] = "admin"
        admin_ids_str = os.environ.get("ADMIN_IDS", "")
        admin_ids = []
        for id_str in admin_ids_str.split(","):
            try:
                admin_ids.append(int(id_str.strip()))
            except ValueError:
                continue
        cfg["admin_ids"] = admin_ids
    else:
        cfg["mode"] = "multi"
    
    # Poll interval
    try:
        poll_interval = int(os.environ.get("POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL)))
        if poll_interval > 0:
            cfg["poll_interval"] = poll_interval
    except ValueError:
        cfg["poll_interval"] = DEFAULT_POLL_INTERVAL
    
    return cfg


def interactive_setup(platform_name: str) -> dict:
    print("=== Initial setup for Power Monitor Bot ===")
    print(f"Detected platform: {platform_name}")
    
    # First try environment variables (for container/non-interactive environments)
    env_cfg = setup_from_env(platform_name)
    if env_cfg:
        print("[INFO] Using configuration from environment variables.")
        print(f"[INFO] Mode: {env_cfg['mode']} | Poll interval: {env_cfg['poll_interval']}s")
        if env_cfg["mode"] == "admin":
            print(f"[INFO] Admin IDs: {env_cfg['admin_ids']}")
        
        # Save and return
        ok = safe_write_json(DATA_FILE, env_cfg)
        if not ok:
            print("[WARN] Failed to save configuration to data.json. Please ensure write permissions.")
        else:
            print(f"[OK] Configuration saved to {DATA_FILE}")
        return env_cfg
    
    # Interactive setup (original method)
    cfg = default_state(platform_name)
    
    try:
        while True:
            token = input("Paste your Telegram Bot Token (from @BotFather) and press Enter: ").strip()
            if token and len(token.split(":")) >= 2:
                cfg["bot_token"] = token
                break
            print("Invalid token format. Try again (it should look like 123456:ABC-... ).")

        while True:
            mode = input("Choose mode: (1) admin-only  (2) multi-user. Enter 1 or 2: ").strip()
            if mode == "1":
                cfg["mode"] = "admin"
                ids_raw = input("Enter admin chat id(s), comma-separated (you can add your Telegram numeric chat id now): ").strip()
                admin_ids = []
                for p in [x.strip() for x in ids_raw.split(",") if x.strip()]:
                    try:
                        admin_ids.append(int(p))
                    except Exception:
                        print(f"Skipping invalid id: {p}")
                cfg["admin_ids"] = admin_ids
                print(f"Admin IDs set to: {cfg['admin_ids']}")
                break
            elif mode == "2":
                cfg["mode"] = "multi"
                print("Multi-user mode selected: anyone can /start to register.")
                break
            else:
                print("Please type 1 or 2.")

        # poll interval
        while True:
            pi = input(f"Polling interval in seconds [{DEFAULT_POLL_INTERVAL}]: ").strip()
            if pi == "":
                cfg["poll_interval"] = DEFAULT_POLL_INTERVAL
                break
            try:
                v = int(pi)
                if v < 1:
                    raise ValueError()
                cfg["poll_interval"] = v
                break
            except Exception:
                print("Enter a positive integer.")

    except (EOFError, KeyboardInterrupt):
        print("\n[ERROR] Interactive setup failed (no terminal input available).")
        print("For container/non-interactive environments, set these environment variables:")
        print("  BOT_TOKEN=add your bot token")
        print("  BOT_MODE=multi (or 'admin' for admin-only mode)")
        print("  ADMIN_IDS=your telegram id ")#comma-separated, required for admin mode
        print("  POLL_INTERVAL=5 (optional, defaults to 5 seconds)")
        sys.exit(1)

    # save
    ok = safe_write_json(DATA_FILE, cfg)
    if not ok:
        print("[WARN] Failed to save configuration to data.json. Please ensure write permissions.")
    else:
        print(f"[OK] Configuration saved to {DATA_FILE}")
    return cfg


# ----------------------------
# Telegram bot and logic
# ----------------------------
class PowerMonitorBot:
    def __init__(self, config: dict):
        self.config = config
        self.platform = config.get("platform", detect_platform())
        self.poll_interval = int(config.get("poll_interval", DEFAULT_POLL_INTERVAL))
        self.token = config.get("bot_token")
        if not self.token:
            raise ValueError("Bot token missing in configuration.")
        self.bot = telebot.TeleBot(self.token)
        self._monitor_thread = None
        self._stop_event = threading.Event()
        # set of chat ids who should receive alerts
        self.registered_ids = set([int(x) for x in config.get("registered_ids", []) if x])
        self.admin_ids = set([int(x) for x in config.get("admin_ids", []) if x])
        self.mode = config.get("mode", "multi")
        self.prev_plugged = None
        # register handlers
        self._register_handlers()

    def _is_allowed(self, chat_id: int) -> bool:
        if self.mode == "multi":
            return True
        # admin mode
        return chat_id in self.admin_ids

    def _create_main_menu(self, chat_id: int) -> InlineKeyboardMarkup:
        """Create main menu with emoji buttons for all commands"""
        keyboard = InlineKeyboardMarkup(row_width=2)
        
        # Add main command buttons
        keyboard.add(
            InlineKeyboardButton("🔋 Battery Status", callback_data="status"),
            InlineKeyboardButton("❓ Help", callback_data="help")
        )
        
        keyboard.add(
            InlineKeyboardButton("📝 Register", callback_data="register"),
            InlineKeyboardButton("🚫 Unregister", callback_data="unregister")
        )
        
        # Add admin-only button if user is admin
        if chat_id in self.admin_ids:
            keyboard.add(
                InlineKeyboardButton("⚙️ Reconfigure (Admin)", callback_data="reconfigure")
            )
        
        return keyboard

    def _register_handlers(self):
        @self.bot.message_handler(commands=["start"])
        def handle_start(message):
            cid = message.chat.id
            if self.mode == "admin" and cid not in self.admin_ids:
                self.bot.reply_to(message, "🚫 Access denied. This bot is running in admin-only mode.")
                return
            
            # Show welcome message with main menu
            welcome_msg = "🔋 Welcome to Power Monitor Bot!\n\n"
            welcome_msg += "Choose an option from the menu below:"
            
            keyboard = self._create_main_menu(cid)
            self.bot.send_message(cid, welcome_msg, reply_markup=keyboard)

        @self.bot.message_handler(commands=["status"])
        def handle_status(message):
            cid = message.chat.id
            if not self._is_allowed(cid):
                self.bot.reply_to(message, "🚫 You are not allowed to use this bot.")
                return
            snap = get_battery_snapshot(self.platform)
            status_msg = f"🔋 **Current Status:** {snapshot_text(snap)}"
            keyboard = self._create_main_menu(cid)
            self.bot.send_message(cid, status_msg, reply_markup=keyboard, parse_mode='Markdown')

        @self.bot.message_handler(commands=["unregister"])
        def handle_unregister(message):
            cid = message.chat.id
            if cid in self.registered_ids:
                self.registered_ids.remove(cid)
                self._persist_registered()
                msg = "✅ You have been unregistered from alerts."
            else:
                msg = "ℹ️ You are not currently registered."
            
            keyboard = self._create_main_menu(cid)
            self.bot.send_message(cid, msg, reply_markup=keyboard, parse_mode='Markdown')

        @self.bot.message_handler(commands=["help"])
        def handle_help(message):
            cid = message.chat.id
            help_msg = "❓ **Power Monitor Bot Help**\n\n"
            help_msg += "Available commands:\n"
            help_msg += "🔋 **Battery Status** - Get current power/battery snapshot\n"
            help_msg += "📝 **Register** - Register for power alerts\n"
            help_msg += "🚫 **Unregister** - Stop receiving alerts\n"
            help_msg += "❓ **Help** - Show this help message\n"
            
            if cid in self.admin_ids:
                help_msg += "⚙️ **Reconfigure** - Re-run configuration (admin only)\n"
            
            help_msg += "\nUse the buttons below to interact with the bot:"
            
            keyboard = self._create_main_menu(cid)
            self.bot.send_message(cid, help_msg, reply_markup=keyboard, parse_mode='Markdown')

        @self.bot.message_handler(commands=["reconfigure"])
        def handle_reconfigure(message):
            cid = message.chat.id
            if cid not in self.admin_ids:
                self.bot.reply_to(message, "🚫 Only admin(s) can reconfigure the bot.")
                return
            
            msg = "⚙️ **Reconfiguration requested**\n\n"
            msg += "Please watch the console where the bot runs for configuration prompts."
            keyboard = self._create_main_menu(cid)
            self.bot.send_message(cid, msg, reply_markup=keyboard, parse_mode='Markdown')
            # run reconfigure in a new thread so handler returns
            threading.Thread(target=self._reconfigure_console, daemon=True).start()

        @self.bot.callback_query_handler(func=lambda call: True)
        def handle_callback_query(call):
            cid = call.message.chat.id
            data = call.data
            
            # Answer the callback query to remove loading state
            self.bot.answer_callback_query(call.id)
            
            if data == "status":
                if not self._is_allowed(cid):
                    self.bot.send_message(cid, "🚫 You are not allowed to use this bot.")
                    return
                snap = get_battery_snapshot(self.platform)
                status_msg = f"🔋 **Current Status:** {snapshot_text(snap)}"
                keyboard = self._create_main_menu(cid)
                self.bot.edit_message_text(status_msg, cid, call.message.message_id, 
                                         reply_markup=keyboard, parse_mode='Markdown')
                
            elif data == "register":
                if self.mode == "admin" and cid not in self.admin_ids:
                    self.bot.send_message(cid, "🚫 Access denied. This bot is running in admin-only mode.")
                    return
                    
                if cid not in self.registered_ids:
                    self.registered_ids.add(cid)
                    self._persist_registered()
                    msg = "✅ Successfully registered for power alerts!"
                    # Show current status after registration
                    snap = get_battery_snapshot(self.platform)
                    msg += f"\n\n🔋 **Current Status:** {snapshot_text(snap)}"
                else:
                    msg = "ℹ️ You are already registered for power alerts."
                    
                keyboard = self._create_main_menu(cid)
                self.bot.edit_message_text(msg, cid, call.message.message_id, 
                                         reply_markup=keyboard, parse_mode='Markdown')
                
            elif data == "unregister":
                if cid in self.registered_ids:
                    self.registered_ids.remove(cid)
                    self._persist_registered()
                    msg = "✅ You have been unregistered from alerts."
                else:
                    msg = "ℹ️ You are not currently registered."
                    
                keyboard = self._create_main_menu(cid)
                self.bot.edit_message_text(msg, cid, call.message.message_id, 
                                         reply_markup=keyboard, parse_mode='Markdown')
                
            elif data == "help":
                help_msg = "❓ **Power Monitor Bot Help**\n\n"
                help_msg += "Available commands:\n"
                help_msg += "🔋 **Battery Status** - Get current power/battery snapshot\n"
                help_msg += "📝 **Register** - Register for power alerts\n"
                help_msg += "🚫 **Unregister** - Stop receiving alerts\n"
                help_msg += "❓ **Help** - Show this help message\n"
                
                if cid in self.admin_ids:
                    help_msg += "⚙️ **Reconfigure** - Re-run configuration (admin only)\n"
                
                help_msg += "\nUse the buttons below to interact with the bot:"
                
                keyboard = self._create_main_menu(cid)
                self.bot.edit_message_text(help_msg, cid, call.message.message_id, 
                                         reply_markup=keyboard, parse_mode='Markdown')
                
            elif data == "reconfigure":
                if cid not in self.admin_ids:
                    self.bot.send_message(cid, "🚫 Only admin(s) can reconfigure the bot.")
                    return
                    
                msg = "⚙️ **Reconfiguration requested**\n\n"
                msg += "Please watch the console where the bot runs for configuration prompts."
                keyboard = self._create_main_menu(cid)
                self.bot.edit_message_text(msg, cid, call.message.message_id, 
                                         reply_markup=keyboard, parse_mode='Markdown')
                # run reconfigure in a new thread so handler returns
                threading.Thread(target=self._reconfigure_console, daemon=True).start()

    def _persist_registered(self):
        # update config and persist to data.json
        try:
            cfg = safe_read_json(DATA_FILE) or {}
            cfg.setdefault("registered_ids", [])
            cfg["registered_ids"] = list(self.registered_ids)
            # admin ids should persist too
            cfg["admin_ids"] = list(self.admin_ids)
            cfg["mode"] = self.mode
            cfg["poll_interval"] = self.poll_interval
            # ensure platform and token remain
            cfg["platform"] = self.platform
            cfg["bot_token"] = self.token
            safe_write_json(DATA_FILE, cfg)
        except Exception as e:
            print(f"[WARN] Failed to persist registered ids: {e}")

    def _reconfigure_console(self):
        """
        Allow admin to change configuration via console during runtime.
        This will re-save data.json and update runtime state.
        """
        print("[INFO] Starting interactive reconfiguration.")
        new_cfg = interactive_setup(self.platform)
        # Replace runtime fields
        self.token = new_cfg.get("bot_token", self.token)
        # NOTE: bot token change requires re-creating telebot; quitting and re-run recommended.
        self.mode = new_cfg.get("mode", self.mode)
        self.admin_ids = set(new_cfg.get("admin_ids", []))
        self.poll_interval = int(new_cfg.get("poll_interval", self.poll_interval))
        # update registered list in file
        self._persist_registered()
        print("[INFO] Reconfiguration complete. If you changed the bot token, please restart the script to apply it.")

    def _notify_all(self, text: str):
        recipients = list(self.registered_ids) if self.mode == "multi" else list(self.admin_ids)
        if not recipients:
            print("[INFO] No registered recipients to notify.")
            return
        for cid in recipients:
            try:
                self.bot.send_message(cid, text, parse_mode='Markdown')
            except Exception as e:
                print(f"[WARN] Failed to send message to {cid}: {e}")

    def start_monitoring(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        print("[INFO] Monitoring thread started.")

    def stop(self):
        self._stop_event.set()

    def _monitor_loop(self):
        print(f"[INFO] Monitor loop running. Poll interval = {self.poll_interval}s")
        # Initialize prev_plugged on first read
        snap = get_battery_snapshot(self.platform)
        if snap:
            self.prev_plugged = snap.get("power_plugged")
        else:
            self.prev_plugged = None
            print("[WARN] Battery snapshot not available. Automatic transition alerts will be disabled.")
        while not self._stop_event.is_set():
            try:
                snap = get_battery_snapshot(self.platform)
                if snap:
                    plugged = snap.get("power_plugged")
                    # On first actual reading set prev if None
                    if self.prev_plugged is None:
                        self.prev_plugged = plugged
                    elif plugged is not None and plugged != self.prev_plugged:
                        # Transition happened
                        if plugged:
                            msg = "🔌 **Power is ON now**"
                        else:
                            msg = "⚡ **Power is OFF now**"
                        print(f"[EVENT] Transition detected. Sending notifications. ({'plugged' if plugged else 'unplugged'})")
                        self._notify_all(msg)
                        self.prev_plugged = plugged
                else:
                    # no snapshot available; skip
                    pass
            except Exception as e:
                print(f"[ERROR] Exception in monitor loop: {e}")
            time.sleep(self.poll_interval)

    def run(self):
        # start background monitor
        self.start_monitoring()
        # start bot polling (blocking)
        print("[INFO] Starting Telegram bot polling. Press Ctrl+C to stop.")
        try:
            # infinity_polling will keep running and handle network reconnects
            self.bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except KeyboardInterrupt:
            print("[INFO] Keyboard interrupt received; shutting down.")
        except Exception as e:
            print(f"[ERROR] Bot polling error: {e}")
        finally:
            self.stop()


# ----------------------------
# Main entry point
# ----------------------------
def main():
    platform_name = detect_platform()
    cfg = safe_read_json(DATA_FILE)
    if not cfg:
        # run interactive setup
        cfg = interactive_setup(platform_name)
    else:
        # ensure platform recorded; if different, update and persist
        if cfg.get("platform") != platform_name:
            cfg["platform"] = platform_name
            safe_write_json(DATA_FILE, cfg)

    # sanity checks
    token = cfg.get("bot_token")
    if not token:
        print("[ERROR] No bot token found in data.json. Run the script and provide token when prompted.")
        sys.exit(1)

    print(f"[INFO] Platform detected: {platform_name}")
    print(f"[INFO] Mode: {cfg.get('mode')} | Poll interval: {cfg.get('poll_interval', DEFAULT_POLL_INTERVAL)}s")
    # instantiate bot
    try:
        pm = PowerMonitorBot(cfg)
    except Exception as e:
        print(f"[ERROR] Failed to initialize bot: {e}")
        sys.exit(1)

    # pre-populate registered ids from config
    reg = cfg.get("registered_ids", [])
    for r in reg:
        try:
            pm.registered_ids.add(int(r))
        except Exception:
            pass
    for a in cfg.get("admin_ids", []):
        try:
            pm.admin_ids.add(int(a))
        except Exception:
            pass

    # run
    pm.run()


if __name__ == "__main__":
    main()
