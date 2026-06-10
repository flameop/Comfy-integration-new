# -*- coding: utf-8 -*-
from __future__ import print_function

"""
ComfyUI Integration for Flame
Version: 2.8.4 - Updated CO_urls to CO_info_links
"""

import os
import re
import time
import datetime
import json
import platform
import socket
import urllib.request
import shutil
import traceback

try:
    import flame
except ImportError:
    flame = None

# Support both PySide6 (Newer Flame) and PySide2 (Older Flame)
try:
    from PySide6 import QtWidgets, QtCore
except ImportError:
    from PySide2 import QtWidgets, QtCore

# ================== GLOBALS & CONSTANTS ==================

__version__ = "2.8.4"

_HOME = os.path.expanduser("~")
_IS_LINUX = platform.system() == 'Linux'

if _IS_LINUX:
    _COMFY_BASE = os.path.join(_HOME, "ComfyUI")
else:
    _COMFY_BASE = os.path.join(_HOME, "Documents", "ComfyUI")

# Persistent storage paths
CO_PROFILES_DIR = "/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/workflows/CO_FlameIntegrations/CO_Profiles"
CO_SENT_DIR = "/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/workflows/CO_FlameIntegrations/CO_Sent_Profiles"
CO_ARCHIVE_DIR = "/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/workflows/CO_FlameIntegrations/CO_Sent_Profile_Archive"

DEFAULT_CONFIG = {
    "comfy_url": "http://127.0.0.1",
    "comfy_port": 8188 if _IS_LINUX else 8000,
    "comfy_input_dir": os.path.join(_COMFY_BASE, "input", "Flame_outputs"),
    "comfy_output_dir": os.path.join(_COMFY_BASE, "output", "flame_returns"),
    "preset_path": "/opt/Autodesk/shared/python/CO_ComfyIntegration/export_presets/EXPORT_PNG_COMFYUI.xml",
    "export_format": "PNG 8-bit",
    "presets_dir": "/opt/Autodesk/shared/python/CO_ComfyIntegration/export_presets",
    "workflows_dir": os.path.join(_COMFY_BASE, "flame_comfy_workflows"),
    "pipeline_export_path": "",
    "pipeline_result_path": "",
    "output_format": "png",
    "output_quality": 95,
    "colorspace": "default",
    "timeout": 300,
    "auto_import": True,
    "import_destination": "batch",
    "open_browser_manual": True,
    "show_notifications": True,
    "connection_mode": "local",
    "remote_ssh_host": "user@remote-host",
    "remote_path": "/home/user/ComfyUI/input/Flame_outputs",
}

EXPORT_PRESETS = {
    "PNG 8-bit": "EXPORT_PNG_COMFYUI.xml",
    "PNG 16-bit": "EXPORT_PNG16_COMFYUI.xml",
    "EXR 16-bit float": "EXPORT_EXR_COMFYUI.xml",
    "EXR 32-bit float": "EXPORT_EXR32_COMFYUI.xml",
    "JPEG 8-bit": "EXPORT_JPEG_COMFYUI.xml",
}

FILE_WAIT_TIMEOUT = 60

# ================== LOGGING & UTILITIES ==================

def log(msg):
    x = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[ComfyUI] {x}: {msg}"
    print(full_msg)
    try:
        if flame:
            flame.messages.show_in_console(msg, duration=0)
    except:
        pass

def sanitize_filename(name):
    return re.sub(r'[<>:"/\\|?*]', "_", name)

def sanitize_hostname(name):
    return re.sub(r'[^a-zA-Z0-9.-]', "_", name)

# ================== SEED QUEUE ==================

_seed_queue = []

def add_seed(seed):
    _seed_queue.append(str(seed))

def get_next_seed():
    return _seed_queue.pop(0) if _seed_queue else "0"

# ================== CONFIGURATION MANAGEMENT ==================

class ConfigManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._config_file = os.path.expanduser("~/.flame_comfy_config.json")
            cls._instance.config = cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        config = DEFAULT_CONFIG.copy()
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, 'r') as f:
                    user_config = json.load(f)
                    config.update(user_config)
            except Exception as e:
                log(f"Config load error: {e}")
        return config
    
    def save_config(self, new_config=None):
        if new_config:
            self.config.update(new_config)
        try:
            with open(self._config_file, 'w') as f:
                json.dump(self.config, f, indent=2)
            log(f"Configuration saved: {self._config_file}")
            return True
        except Exception as e:
            log(f"Config save error: {e}")
            return False

    def reload(self):
        self.config = self._load_config()

    def get(self, key, default=None):
        return self.config.get(key, default)

    @property
    def url(self):
        return f"{self.config['comfy_url'].rstrip('/')}:{self.config['comfy_port']}"
        
    @property
    def input_dir(self):
        return self.config['comfy_input_dir']
        
    @property
    def output_dir(self):
        return self.config['comfy_output_dir']

    @property
    def notification_file(self):
        return os.path.join(self.output_dir, 'notification.json')

    @property
    def pipeline_notification_file(self):
        pipe_res = self.config.get('pipeline_result_path', '')
        if pipe_res:
            return os.path.join(pipe_res, 'notification.json')
        return None

    @property
    def workflows_dir(self):
        return self.config['workflows_dir']

    def get_preset_path(self, export_format):
        presets_dir = self.config.get('presets_dir', DEFAULT_CONFIG['presets_dir'])
        preset_file = EXPORT_PRESETS.get(export_format, '')
        if preset_file:
            return os.path.join(presets_dir, preset_file)
        return self.config.get('preset_path', DEFAULT_CONFIG['preset_path'])


# ================== WATCHER / AUTO-IMPORT ==================

def _resolve_flame_tokens(path_template, clip_name=''):
    result = path_template
    now = datetime.datetime.now()
    replacements = {
        '<date>': now.strftime('%Y-%m-%d'),
        '<time>': now.strftime('%H%M%S'),
        '<YYYY>': now.strftime('%Y'),
        '<YY>': now.strftime('%y'),
        '<MM>': now.strftime('%m'),
        '<DD>': now.strftime('%d'),
        '<hh>': now.strftime('%H'),
        '<mm>': now.strftime('%M'),
        '<ss>': now.strftime('%S'),
        '<clip name>': clip_name,
    }
    try:
        if flame:
            project = flame.project.current_project
            replacements['<project>'] = _get_flame_attr_str(project.name)
            try: replacements['<project nickname>'] = _get_flame_attr_str(project.nickname)
            except: pass
            try: replacements['<user>'] = _get_flame_attr_str(flame.users.current_user.name)
            except: pass
            try: replacements['<user nickname>'] = _get_flame_attr_str(flame.users.current_user.nickname)
            except: pass
            try: replacements['<workstation>'] = socket.gethostname()
            except: pass
            try:
                if hasattr(flame, 'batch') and flame.batch:
                    replacements['<batch name>'] = _get_flame_attr_str(flame.batch.name)
            except: pass
    except:
        pass
    for token, value in replacements.items():
        result = result.replace(token, value)
    return result

def _build_import_pattern(folder_path, files):
    if not files:
        return None
    if len(files) == 1:
        return os.path.join(folder_path, files[0])
    match = re.search(r'(\d{4,})\.[^\.]+$', files[0])
    if match:
        seq_num = match.group(1)
        padding = len(seq_num)
        last_match = re.search(r'(\d{4,})\.[^\.]+$', files[-1])
        if last_match:
            start_str = str(int(seq_num)).zfill(padding)
            end_str = str(int(last_match.group(1))).zfill(padding)
            ext_part = files[0][match.end(1):]
            prefix = files[0][:match.start(1)]
            pattern_name = f"{prefix}[{start_str}-{end_str}]{ext_part}"
            return os.path.join(folder_path, pattern_name)
    return os.path.join(folder_path, files[0])

def _do_import_in_idle(notification, output_dir):
    if not flame:
        log("[Watcher] Flame not available for import")
        return

    output_folder = notification.get('output_folder', '')
    clip_name = notification.get('clip_name', output_folder)
    pipeline_folder = notification.get('pipeline_folder', '')

    if pipeline_folder and os.path.exists(pipeline_folder):
        folder_path = pipeline_folder
    else:
        folder_path = os.path.join(output_dir, output_folder)

    if not os.path.exists(folder_path):
        log(f"[Watcher] ERROR: Folder not found: {folder_path}")
        return

    timestamp = datetime.datetime.now().strftime("%H%M%S")
    flame_seed = get_next_seed()
    
    new_name = f"{clip_name}_comfyui_{timestamp}_{flame_seed}"

    cfg = ConfigManager()
    import_destination = cfg.get('import_destination', 'batch')

    try:
        if import_destination == 'library':
            workspace = flame.project.current_project.current_workspace
            comfyui_lib = None
            for lib in (workspace.libraries or []):
                if _get_flame_attr_str(lib.name) == "ComfyUI":
                    comfyui_lib = lib
                    break
            if comfyui_lib is None:
                comfyui_lib = workspace.create_library("ComfyUI")
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            date_folder = None
            for folder in comfyui_lib.folders:
                if _get_flame_attr_str(folder.name) == today:
                    date_folder = folder
                    break
            if not date_folder:
                date_folder = comfyui_lib.create_folder(today)
            flame.import_clips(folder_path, date_folder)
        else:
            if not (hasattr(flame, 'batch') and flame.batch):
                return 
            reel_name = "ComfyUI Results"
            target_reel = None
            for reel in flame.batch.reels:
                if _get_flame_attr_str(reel.name) == reel_name:
                    target_reel = reel
                    break
            if not target_reel:
                target_reel = flame.batch.create_reel(reel_name)
            files = sorted([f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))])
            import_path = _build_import_pattern(folder_path, files)
            if not import_path:
                return
            batch_clip = flame.batch.import_clip(import_path, reel_name)
            if batch_clip:
                try:
                    if hasattr(batch_clip, 'name'):
                        if hasattr(batch_clip.name, 'set_value'):
                            batch_clip.name.set_value(new_name)
                        else:
                            batch_clip.name = new_name
                except: pass
        try:
            flame.messages.show_in_console(f"ComfyUI result imported: {new_name}", duration=5)
        except: pass
        for notif_path in [os.path.join(output_dir, 'notification.json')]:
            try:
                if os.path.exists(notif_path): os.remove(notif_path)
            except: pass
    except Exception as e:
        log(f"[Watcher] ERROR during idle import: {e}")

class ComfyUIWatcher(QtCore.QObject):
    def __init__(self, output_dir, notification_file, check_interval=3000, pipeline_notification_file=None):
        app = QtCore.QCoreApplication.instance()
        super().__init__(app)
        self.output_dir = output_dir
        self.notification_file = notification_file
        self.pipeline_notification_file = pipeline_notification_file
        self.check_interval = check_interval
        self.last_notification_time = None
        self.imported_folders = set()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._check_notification)

    def start(self):
        if not self.timer.isActive():
            self._clean_stale_notification()
            self.timer.start(self.check_interval)

    def stop(self):
        if self.timer.isActive(): self.timer.stop()

    def _clean_stale_notification(self):
        for notif_path in [self.notification_file, self.pipeline_notification_file]:
            if not notif_path or not os.path.exists(notif_path): continue
            try:
                with open(notif_path, 'r') as f:
                    data = json.load(f)
                folder = data.get('output_folder', '')
                if folder: self.imported_folders.add(folder)
                os.remove(notif_path)
            except:
                try: os.remove(notif_path)
                except: pass

    def _check_notification(self):
        for notif_path in [self.notification_file, self.pipeline_notification_file]:
            if not notif_path or not os.path.exists(notif_path): continue
            try:
                with open(notif_path, 'r') as f:
                    notification = json.load(f)
                timestamp = notification.get('timestamp', '')
                output_folder = notification.get('output_folder', '')
                if not output_folder or output_folder in self.imported_folders:
                    try: os.remove(notif_path)
                    except: pass
                    continue
                if self.last_notification_time and timestamp <= self.last_notification_time: continue
                self.last_notification_time = timestamp
                self.imported_folders.add(output_folder)
                try: os.remove(notif_path)
                except: pass
                if flame and hasattr(flame, 'schedule_idle_event'):
                    flame.schedule_idle_event(lambda n=notification, d=self.output_dir: _do_import_in_idle(n, d))
                return
            except: pass

_watcher = None

def start_watcher(output_dir, notification_file, pipeline_notification_file=None):
    global _watcher
    if _watcher: _watcher.stop()
    _watcher = ComfyUIWatcher(output_dir, notification_file, check_interval=3000,
                               pipeline_notification_file=pipeline_notification_file)
    _watcher.start()
    return _watcher

def stop_watcher():
    global _watcher
    if _watcher:
        _watcher.stop()
        _watcher = None

# ================== UI UTILITIES & BASE CLASSES ==================

class UIUtils:
    PYFLAME_FONT = 'Discreet'
    PYFLAME_FONT_SIZE = 13
    FLAME_BG = 'rgb(36, 36, 36)'
    FLAME_MID_BG = 'rgb(45, 45, 45)'
    FLAME_WIDGET_BG = 'rgb(58, 58, 58)'
    FLAME_WIDGET_HOVER = 'rgb(71, 71, 71)'
    FLAME_INPUT_BG = 'rgb(55, 65, 75)'
    FLAME_INPUT_FOCUS = 'rgb(73, 86, 99)'
    FLAME_TEXT = 'rgb(154, 154, 154)'
    FLAME_TEXT_BRIGHT = 'rgb(210, 210, 210)'
    FLAME_TEXT_DIM = 'rgb(100, 100, 100)'
    FLAME_BLUE = 'rgb(0, 110, 175)'
    FLAME_HIGHLIGHT = 'rgb(74, 158, 255)'
    FLAME_BORDER = 'rgb(90, 90, 90)'
    FLAME_DISABLED = 'rgb(54, 54, 54)'

    @classmethod
    def get_flame_stylesheet(cls):
        return f"""
            QDialog {{ background-color: {cls.FLAME_BG}; color: {cls.FLAME_TEXT}; font-family: '{cls.PYFLAME_FONT}'; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QLabel {{ color: {cls.FLAME_TEXT}; background-color: transparent; border: none; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QLabel#header {{ font-size: 15px; color: {cls.FLAME_TEXT_DIM}; font-weight: 300; }}
            QLabel#section {{ font-size: {cls.PYFLAME_FONT_SIZE}px; color: {cls.FLAME_TEXT_DIM}; padding: 10px 0px 6px 0px; }}
            QLineEdit, QPlainTextEdit, QTextEdit {{
                color: {cls.FLAME_TEXT}; background-color: {cls.FLAME_INPUT_BG}; border: 1px solid {cls.FLAME_INPUT_BG};
                selection-color: rgb(38, 38, 38); selection-background-color: rgb(184, 177, 167); padding: 6px 8px; font-size: {cls.PYFLAME_FONT_SIZE}px;
            }}
            QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ background-color: {cls.FLAME_INPUT_FOCUS}; }}
            QLineEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {{ border: 1px solid {cls.FLAME_BORDER}; }}
            QComboBox {{ background-color: {cls.FLAME_WIDGET_BG}; color: {cls.FLAME_TEXT}; border: 1px solid {cls.FLAME_BORDER}; padding: 4px 12px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QComboBox:hover {{ border: 1px solid {cls.FLAME_HIGHLIGHT}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{ background-color: {cls.FLAME_WIDGET_BG}; color: {cls.FLAME_TEXT}; selection-background-color: {cls.FLAME_BLUE}; selection-color: {cls.FLAME_TEXT_BRIGHT}; border: none; }}
            QSpinBox {{ background-color: {cls.FLAME_INPUT_BG}; color: {cls.FLAME_TEXT}; border: 1px solid {cls.FLAME_INPUT_BG}; padding: 6px 8px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QSpinBox:hover {{ border: 1px solid {cls.FLAME_BORDER}; }}
            QSpinBox::up-button, QSpinBox::down-button {{ background-color: transparent; border: none; width: 16px; }}
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background-color: {cls.FLAME_WIDGET_HOVER}; }}
            QPushButton {{ background-color: {cls.FLAME_WIDGET_BG}; color: rgb(165, 165, 165); border: none; padding: 8px 20px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QPushButton:hover {{ border: 1px solid {cls.FLAME_BORDER}; }}
            QPushButton:pressed {{ color: {cls.FLAME_TEXT_BRIGHT}; background-color: {cls.FLAME_WIDGET_HOVER}; }}
            QPushButton:focus {{ outline: none; border: none; }}
            QPushButton#primary {{ background-color: {cls.FLAME_BLUE}; color: rgb(185, 185, 185); }}
            QPushButton#primary:hover {{ border: 1px solid {cls.FLAME_BORDER}; }}
            QPushButton#primary:pressed {{ color: {cls.FLAME_TEXT_BRIGHT}; }}
            QPushButton:disabled {{ color: {cls.FLAME_TEXT_DIM}; background-color: {cls.FLAME_DISABLED}; }}
            QCheckBox {{ color: {cls.FLAME_TEXT}; spacing: 8px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QCheckBox::indicator {{ width: 16px; height: 16px; background-color: {cls.FLAME_WIDGET_BG}; border: none; }}
            QCheckBox::indicator:hover {{ background-color: {cls.FLAME_WIDGET_HOVER}; }}
            QCheckBox::indicator:checked {{ background-color: {cls.FLAME_BLUE}; }}
            QMessageBox {{ background-color: {cls.FLAME_BG}; color: {cls.FLAME_TEXT}; font-family: '{cls.PYFLAME_FONT}'; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QMessageBox QLabel {{ color: {cls.FLAME_TEXT}; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QMessageBox QPushButton {{ background-color: {cls.FLAME_WIDGET_BG}; color: rgb(165, 165, 165); border: none; padding: 6px 15px; min-width: 80px; }}
            QMessageBox QPushButton:hover {{ border: 1px solid {cls.FLAME_BORDER}; }}
        """

class FlameBaseDialog(QtWidgets.QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(UIUtils.get_flame_stylesheet())
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setSpacing(12)
        self.main_layout.setContentsMargins(24, 24, 24, 24)

    def add_header(self, text):
        lbl = QtWidgets.QLabel(text); lbl.setObjectName("header")
        self.main_layout.addWidget(lbl)
        return lbl

    def add_section_label(self, text):
        lbl = QtWidgets.QLabel(text); lbl.setObjectName("section")
        self.main_layout.addWidget(lbl)
        return lbl

    def create_standard_buttons(self, ok_text="OK", cancel_text="Cancel", on_ok=None, on_cancel=None):
        btn_layout = QtWidgets.QHBoxLayout(); btn_layout.addStretch()
        cancel_btn = QtWidgets.QPushButton(cancel_text); cancel_btn.setMinimumSize(110, 28)
        cancel_btn.clicked.connect(on_cancel or self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QtWidgets.QPushButton(ok_text); ok_btn.setObjectName("primary"); ok_btn.setMinimumSize(110, 28)
        ok_btn.setDefault(True); ok_btn.clicked.connect(on_ok or self.accept)
        btn_layout.addWidget(ok_btn)
        return btn_layout

# ================== WORKFLOW MODIFIER ==================

class WorkflowModifier:
    @staticmethod
    def load_workflow(path):
        try:
            with open(path, 'r') as f: workflow = json.load(f)
            is_api = "nodes" not in workflow
            return workflow, is_api
        except Exception as e:
            log(f"Error loading workflow: {e}")
            return None, False

    @staticmethod
    def convert_to_api(workflow):
        api_workflow = {}
        if "nodes" not in workflow: return None
        for node in workflow["nodes"]:
            node_id = str(node["id"])
            api_workflow[node_id] = {"class_type": node["type"], "inputs": {}}
            if node["type"] == "LoadImage" and node.get("widgets_values"):
                api_workflow[node_id]["inputs"]["image"] = node["widgets_values"][0]
        if "links" in workflow:
            for link in workflow["links"]:
                if len(link) >= 5:
                    t_node, t_slot, s_node, s_slot = str(link[3]), link[4], str(link[1]), link[2]
                    if t_node in api_workflow:
                        api_workflow[t_node]["inputs"][f"input_{t_slot}"] = [s_node, s_slot]
        return api_workflow

    @staticmethod
    def inject_profile_path(workflow, is_api, profile_path):
        target_title = "CO_Profile_Sidecar_File"
        if is_api:
            for n_id, n_data in workflow.items():
                if isinstance(n_data, dict):
                    title = n_data.get('_meta', {}).get('title', '')
                    if title == target_title:
                        inputs = n_data.setdefault('inputs', {})
                        found = False
                        for key in ['file_path', 'path', 'string', 'value', 'text']:
                            if key in inputs:
                                inputs[key] = profile_path
                                found = True; break
                        if not found: inputs['file_path'] = profile_path
        else:
            for node in workflow.get("nodes", []):
                if node.get("title", node.get("type")) == target_title:
                    if "widgets_values" not in node: node["widgets_values"] = [profile_path]
                    elif len(node["widgets_values"]) > 0: node["widgets_values"][0] = profile_path
        return workflow

# ================== COMFYUI API WRAPPER ==================

class ComfyAPI:
    @staticmethod
    def test_connection(url):
        try:
            resp = urllib.request.urlopen(f"{url}/system_stats", timeout=5)
            return resp.status == 200, None
        except Exception as e: return False, str(e)

    @staticmethod
    def execute_workflow(workflow, workflow_name):
        cfg = ConfigManager()
        url = cfg.url
        try:
            payload = json.dumps({"prompt": workflow}).encode('utf-8')
            req = urllib.request.Request(f"{url}/prompt", data=payload, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result:
                    log(f"Workflow '{workflow_name}' executed. Prompt ID: {result.get('prompt_id', 'N/A')}")
                    return True
        except Exception as e: log(f"API Execution Error: {e}")
        return False

# ================== DIALOGS: SETTINGS ==================

class ComfyUISettingsDialog(FlameBaseDialog):
    def __init__(self, parent=None):
        super().__init__("ComfyUI Integration - Settings", parent)
        self.cfg = ConfigManager()
        self.setMinimumWidth(700); self.resize(750, 600)
        self.setup_ui()

    def setup_ui(self):
        self.add_header(f"ComfyUI Integration Settings v{__version__}")
        self.add_section_label("Connection")
        conn_layout = QtWidgets.QVBoxLayout()
        conn_form = QtWidgets.QFormLayout()
        self.url_input = QtWidgets.QLineEdit(self.cfg.get('comfy_url'))
        self.port_input = QtWidgets.QSpinBox(); self.port_input.setRange(1000, 65535); self.port_input.setValue(self.cfg.get('comfy_port'))
        conn_form.addRow("URL:", self.url_input); conn_form.addRow("Port:", self.port_input)
        conn_layout.addLayout(conn_form)
        self.test_btn = QtWidgets.QPushButton("Test Connection"); self.test_btn.clicked.connect(self.test_server_connection)
        test_btn_layout = QtWidgets.QHBoxLayout(); test_btn_layout.addStretch(); test_btn_layout.addWidget(self.test_btn)
        conn_layout.addLayout(test_btn_layout); self.main_layout.addLayout(conn_layout)
        self.add_section_label("Export & Media")
        export_form = QtWidgets.QFormLayout()
        self.format_combo = QtWidgets.QComboBox(); self.format_combo.addItems(list(EXPORT_PRESETS.keys()))
        current_fmt = self.cfg.get('export_format', 'PNG 8-bit')
        if current_fmt in EXPORT_PRESETS: self.format_combo.setCurrentText(current_fmt)
        export_form.addRow("Export Format:", self.format_combo)
        self.auto_import_cb = QtWidgets.QCheckBox("Auto-import back to Flame"); self.auto_import_cb.setChecked(self.cfg.get('auto_import', True))
        export_form.addRow("", self.auto_import_cb)
        self.import_dest_combo = QtWidgets.QComboBox(); self.import_dest_combo.addItems(["batch", "media_panel"])
        self.import_dest_combo.setCurrentText(self.cfg.get('import_destination', 'batch'))
        export_form.addRow("Import Destination:", self.import_dest_combo)
        self.main_layout.addLayout(export_form)
        self.add_section_label("Paths")
        paths_form = QtWidgets.QFormLayout()
        self.input_dir = QtWidgets.QLineEdit(self.cfg.input_dir)
        self.output_dir = QtWidgets.QLineEdit(self.cfg.output_dir)
        self.workflows_dir = QtWidgets.QLineEdit(self.cfg.workflows_dir)
        self.presets_dir = QtWidgets.QLineEdit(self.cfg.get('presets_dir', ''))
        self.preset_path = QtWidgets.QLineEdit(self.cfg.get('preset_path', ''))
        paths_form.addRow("ComfyUI Input:", self.input_dir); paths_form.addRow("ComfyUI Output:", self.output_dir)
        paths_form.addRow("Workflows Dir:", self.workflows_dir); paths_form.addRow("Presets Dir:", self.presets_dir); paths_form.addRow("Fallback Preset Path:", self.preset_path)
        self.pipe_res_path = QtWidgets.QLineEdit(self.cfg.get('pipeline_result_path', ''))
        paths_form.addRow("Remote Export Path:", self.pipe_res_path)
        self.main_layout.addLayout(paths_form); self.main_layout.addStretch()
        self.main_layout.addLayout(self.create_standard_buttons("Save", "Cancel", self.save_settings))

    def test_server_connection(self):
        url, port = self.url_input.text().rstrip('/'), self.port_input.value()
        test_url = f"{url}:{port}"
        success, error = ComfyAPI.test_connection(test_url)
        msg = QtWidgets.QMessageBox(self); msg.setStyleSheet(UIUtils.get_flame_stylesheet())
        if success:
            msg.setWindowTitle("Connection Successful"); msg.setText(f"Successfully connected to ComfyUI at:\n{test_url}")
            msg.setIcon(QtWidgets.QMessageBox.Information)
        else:
            msg.setWindowTitle("Connection Failed"); msg.setText(f"Failed to connect to ComfyUI at:\n{test_url}\n\nError: {error}")
            msg.setIcon(QtWidgets.QMessageBox.Critical)
        try: msg.exec_()
        except AttributeError: msg.exec()

    def save_settings(self):
        settings_update = {
            'comfy_url': self.url_input.text(), 'comfy_port': self.port_input.value(),
            'comfy_input_dir': self.input_dir.text(), 'comfy_output_dir': self.output_dir.text(),
            'workflows_dir': self.workflows_dir.text(), 'presets_dir': self.presets_dir.text(),
            'preset_path': self.preset_path.text(), 'export_format': self.format_combo.currentText(),
            'auto_import': self.auto_import_cb.isChecked(), 'import_destination': self.import_dest_combo.currentText(),
            'pipeline_result_path': self.pipe_res_path.text()
        }
        if self.cfg.save_config(settings_update): self.accept()

# ================== DIALOGS: PROFILE EDITOR ==================

class FlameBatchJsonEditor(QtWidgets.QDialog):
    def __init__(self, selection=None, clip_data=None, parent=None):
        super().__init__(parent)
        self.selection = selection
        self.clip_data = clip_data if clip_data is not None else []
        self.setWindowTitle("ComfyUI Workflows")
        self.resize(1300, 750); self.setMinimumWidth(1000)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        self.setStyleSheet("""
            QDialog { background-color: #2e2e2e; }
            QLabel { color: #9a9a9a; }
            QListWidget { background-color: #1a1a1a; color: #ccc; border: none; padding: 5px; }
            QListWidget::item:selected { background-color: #464646; }
            QLineEdit, QPlainTextEdit { background-color: #111; color: #ef9d00; border: none; padding: 6px; }
            QPushButton { background-color: #464646; color: white; font-weight: bold; padding: 6px 12px; border: none; }
            QPushButton:enabled { background-color: #ef9d00; }
            QPushButton:disabled { background-color: #333333; color: #666; }
            QScrollArea { border: none; background-color: #2e2e2e; }
            QWidget#scrollWidget { background-color: #2e2e2e; }
            QComboBox { background-color: #111; color: #ef9d00; border: none; padding: 6px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView { background-color: #1a1a1a; color: #ccc; selection-background-color: #464646; border: none; }
        """)
        self.current_file_path, self.data, self.entries = None, {}, {}
        self.history_items = []  # List of (filename, mtime) tuples for searching
        self.init_ui()
        self.scan_directory()

    def init_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        sidebar_layout = QtWidgets.QVBoxLayout()
        title_lbl = QtWidgets.QLabel("Available Workflow Presets")
        title_lbl.setStyleSheet("font-weight: bold; color: #999; font-size: 14px;")
        sidebar_layout.addWidget(title_lbl)
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.file_list.itemSelectionChanged.connect(self.on_main_file_select)
        sidebar_layout.addWidget(self.file_list)
        
        # History Box
        sent_title_lbl = QtWidgets.QLabel("History")
        sent_title_lbl.setStyleSheet("font-weight: bold; color: #999; font-size: 14px; margin-top: 10px;")
        sidebar_layout.addWidget(sent_title_lbl)
        
        # History Search
        self.history_search = QtWidgets.QLineEdit()
        self.history_search.setPlaceholderText("Search history...")
        self.history_search.textChanged.connect(self.filter_history)
        sidebar_layout.addWidget(self.history_search)
        
        self.sent_list = QtWidgets.QListWidget()
        self.sent_list.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.sent_list.itemSelectionChanged.connect(self.on_sent_file_select)
        sidebar_layout.addWidget(self.sent_list)
        
        # Archive Button
        self.archive_btn = QtWidgets.QPushButton("Archive Old History (>7 Days)")
        self.archive_btn.clicked.connect(self.archive_old_jobs)
        sidebar_layout.addWidget(self.archive_btn)
        
        main_layout.addLayout(sidebar_layout) 
        editor_layout = QtWidgets.QVBoxLayout()
        self.workflow_title_lbl = QtWidgets.QLabel("Select a workflow...")
        self.workflow_title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff; margin-bottom: 5px;")
        editor_layout.addWidget(self.workflow_title_lbl)
        self.workflow_desc_lbl = QtWidgets.QLabel("")
        self.workflow_desc_lbl.setStyleSheet("font-size: 14px; color: #ef9d00; margin-bottom: 15px; font-weight: bold;")
        self.workflow_desc_lbl.setWordWrap(True); self.workflow_desc_lbl.hide()
        editor_layout.addWidget(self.workflow_desc_lbl)
        self.scroll_area = QtWidgets.QScrollArea(); self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QtWidgets.QWidget(); self.scroll_widget.setObjectName("scrollWidget")
        self.form_layout = QtWidgets.QFormLayout(self.scroll_widget); self.form_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self.scroll_area.setWidget(self.scroll_widget); editor_layout.addWidget(self.scroll_area)
        self.save_btn = QtWidgets.QPushButton("Send to ComfyUI"); self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_file); editor_layout.addWidget(self.save_btn, alignment=QtCore.Qt.AlignRight)
        main_layout.addLayout(editor_layout, 1) 

    def scan_directory(self):
        if not os.path.exists(CO_PROFILES_DIR):
            try: os.makedirs(CO_PROFILES_DIR)
            except: return
        if not os.path.exists(CO_SENT_DIR):
            try: os.makedirs(CO_SENT_DIR)
            except: pass
        self.file_list.clear(); self.sent_list.clear(); self.history_items = []
        for f in sorted(os.listdir(CO_PROFILES_DIR)):
            if f.endswith('.json') and os.path.isfile(os.path.join(CO_PROFILES_DIR, f)):
                item = QtWidgets.QListWidgetItem(f.replace('-API_Profile.json', '')); item.setData(QtCore.Qt.UserRole, f)
                self.file_list.addItem(item)
        sent_dir = CO_SENT_DIR
        if os.path.exists(sent_dir):
            files_with_time = []
            for f in os.listdir(sent_dir):
                if f.endswith('.json'):
                    full_path = os.path.join(sent_dir, f)
                    files_with_time.append((f, os.path.getmtime(full_path)))
            files_with_time.sort(key=lambda x: x[1], reverse=True)
            self.history_items = files_with_time
            self.update_history_list()
        
        max_width = 120; font_metrics = self.file_list.fontMetrics()
        for i in range(self.file_list.count()):
            text_width = font_metrics.horizontalAdvance(self.file_list.item(i).text()) if hasattr(font_metrics, 'horizontalAdvance') else font_metrics.width(self.file_list.item(i).text())
            max_width = max(max_width, text_width)
        
        # Keep extra padding for the timestamp strings
        final_width = max_width + 180 
        self.file_list.setFixedWidth(final_width)
        self.sent_list.setFixedWidth(final_width)

    def update_history_list(self, filter_text=""):
        self.sent_list.clear()
        for f, mtime in self.history_items:
            display_name = f.replace('.json', '')
            if filter_text.lower() in display_name.lower():
                # --- MODIFIED: Timestamp at the beginning ---
                ts_str = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                full_display = f"[{ts_str}]  {display_name}"
                
                item = QtWidgets.QListWidgetItem(full_display)
                item.setData(QtCore.Qt.UserRole, f)
                self.sent_list.addItem(item)

    def filter_history(self):
        self.update_history_list(self.history_search.text())

    def archive_old_jobs(self):
        sent_dir = CO_SENT_DIR
        if not os.path.exists(sent_dir):
            return
            
        now = time.time()
        count = 0
        
        # Use the global CO_ARCHIVE_DIR as the base for the dated folders
        for f, mtime in self.history_items:
            # Check if file is older than 7 days (7 days * 24 hours * 3600 seconds)
            if (now - mtime) > (7 * 24 * 3600):
                file_date = datetime.datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
                
                # Create the full path for the dated archive folder
                target_archive_path = os.path.join(CO_ARCHIVE_DIR, file_date)
                
                try:
                    # Create the dated directory if it doesn't exist
                    if not os.path.exists(target_archive_path):
                        os.makedirs(target_archive_path)
                    
                    src_file = os.path.join(sent_dir, f)
                    dst_file = os.path.join(target_archive_path, f)
                    
                    # Only move if the source file actually exists on disk
                    if os.path.exists(src_file):
                        shutil.move(src_file, dst_file)
                        count += 1
                except Exception as e:
                    log(f"Archive Error for {f}: {e}")

        if count > 0:
            log(f"Archived {count} files to {CO_ARCHIVE_DIR}")
            self.scan_directory() # Refresh the UI lists
            QtWidgets.QMessageBox.information(self, "Archiving Complete", f"Moved {count} older files to archive.")
    
    def on_main_file_select(self):
        selected = self.file_list.selectedItems()
        if not selected: return
        self.sent_list.blockSignals(True); self.sent_list.clearSelection(); self.sent_list.blockSignals(False)
        self.current_file_path = os.path.join(CO_PROFILES_DIR, selected[0].data(QtCore.Qt.UserRole))
        self.load_selected_file()

    def on_sent_file_select(self):
        selected = self.sent_list.selectedItems()
        if not selected: return
        self.file_list.blockSignals(True); self.file_list.clearSelection(); self.file_list.blockSignals(False)
        self.current_file_path = os.path.join(CO_SENT_DIR, selected[0].data(QtCore.Qt.UserRole))
        self.load_selected_file()

    def load_selected_file(self):
        try:
            with open(self.current_file_path, 'r') as f: self.data = json.load(f)
            workstation = sanitize_hostname(socket.gethostname().replace(".local", ""))
            self.data["CO_WorkstationName"] = workstation
            if "CO_ReturnPath" in self.data:
                clip_name_str = self.clip_data[0][0] if self.clip_data else "UnknownClip"
                self.data["CO_ReturnPath"] = f"/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/output/ToFlame_{workstation}/{clip_name_str}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}/"
            self.save_btn.setEnabled(True); self.refresh_ui()
        except Exception as e: QtWidgets.QMessageBox.critical(self, "Error", str(e))

    def refresh_ui(self):
        self.workflow_title_lbl.setText(f"Workflow: {self.data.get('CO_WorkflowName', 'Unnamed')}")
        workflow_desc = self.data.get("CO_Description", "")
        if workflow_desc: self.workflow_desc_lbl.setText(workflow_desc); self.workflow_desc_lbl.show()
        else: self.workflow_desc_lbl.hide()
        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.entries = {}
        hidden = ["CO_WorkflowName", "CO_WorkstationName", "CO_ReturnPath", "CO_Description"]
        matte_clips, main_clips, unassigned_clips = [], [], []
        for c_name, c_path in self.clip_data:
            c_l = c_name.lower()
            if any(x in c_l for x in ['matte', 'alpha', 'mask']): matte_clips.append((c_name, c_path))
            elif any(x in c_l for x in ['main', 'fg']): main_clips.append((c_name, c_path))
            else: unassigned_clips.append((c_name, c_path))
        used_fallback = set()
        for key, value in self.data.items():
            if value is None or key in hidden or "timestamp" in key.lower(): continue
            if isinstance(value, (dict, list)):
                self.form_layout.addRow(f"{key}:", QtWidgets.QLabel("Nested Data (Uneditable)"))
                continue
            if key == "CO_info_links":
                # Create a dynamic parsed breakdown layout for comma separated URL lists
                url_container = QtWidgets.QWidget()
                url_layout = QtWidgets.QVBoxLayout(url_container)
                url_layout.setContentsMargins(0, 0, 0, 0)
                url_layout.setSpacing(4)
                
                # Raw text entry so users can still modify the raw comma-separated list
                raw_edit = QtWidgets.QLineEdit(str(value))
                url_layout.addWidget(raw_edit)
                
                # Sub-panel listing out individual URLs cleanly
                url_list_str = str(value).split(',')
                for explicit_url in url_list_str:
                    explicit_url = explicit_url.strip()
                    if explicit_url:
                        lbl = QtWidgets.QLabel(f"  • {explicit_url}")
                        lbl.setStyleSheet("color: #74158ff; font-style: italic;") 
                        url_layout.addWidget(lbl)
                        
                self.form_layout.addRow("Info Links List (CO_info_links):", url_container)
                self.entries[key] = raw_edit
                continue
            if key.endswith('ImageSrcPath') or key.endswith('ImageSrc'):
                combo = QtWidgets.QComboBox(); key_l = key.lower()
                target = []
                if any(x in key_l for x in ['matte', 'alpha', 'mask']): target = matte_clips
                elif any(x in key_l for x in ['main', 'fg']): target = main_clips
                else: target = unassigned_clips
                if not target: target = self.clip_data
                for c_n, c_p in target: combo.addItem(c_n, c_p)
                sel_idx = 0
                if target in (unassigned_clips, self.clip_data):
                    for i, (c_n, c_p) in enumerate(target):
                        if c_p not in used_fallback: sel_idx = i; used_fallback.add(c_p); break
                combo.setCurrentIndex(sel_idx); self.form_layout.addRow(f"{key}:", combo); self.entries[key] = combo
                continue
            if key in ["CO_PosPrompt", "CO_NegPrompt"]:
                text = QtWidgets.QPlainTextEdit(str(value)); text.setMinimumHeight(80)
            else: text = QtWidgets.QLineEdit(str(value))
            self.form_layout.addRow(f"{key}:", text); self.entries[key] = text

    def save_file(self):
        for key, entry in self.entries.items():
            if isinstance(entry, QtWidgets.QComboBox): val = str(entry.currentData())
            elif isinstance(entry, QtWidgets.QPlainTextEdit): val = entry.toPlainText().strip()
            else: val = entry.text().strip()
            if val.lower() == "true": self.data[key] = True
            elif val.lower() == "false": self.data[key] = False
            else:
                try: self.data[key] = float(val) if '.' in val else int(val)
                except: self.data[key] = val
        try:
            ts = datetime.datetime.now().strftime("%H%M%S")
            base = re.sub(r'_\d{6}$', '', os.path.splitext(os.path.basename(self.current_file_path))[0])
            sent_dir = CO_SENT_DIR
            if not os.path.exists(sent_dir): os.makedirs(sent_dir)
            new_file_path = os.path.join(sent_dir, f"{base}_{ts}.json")
            with open(new_file_path, 'w') as f: json.dump(self.data, f, indent=4)
            wf_name = self.data.get("CO_WorkflowName")
            if wf_name and wf_name != "Unnamed Workflow":
                cfg = ConfigManager()
                wf_path = os.path.join(cfg.workflows_dir, "CO_APIs", f"{wf_name}.json")
                if os.path.exists(wf_path):
                    wf, is_api = WorkflowModifier.load_workflow(wf_path)
                    if wf:
                        if not is_api: wf = WorkflowModifier.convert_to_api(wf); is_api = True
                        if wf:
                            wf = WorkflowModifier.inject_profile_path(wf, is_api, new_file_path)
                            ComfyAPI.execute_workflow(wf, wf_name)
                else: log(f"Warning: Workflow not found in CO_APIs: {wf_path}")
            self.scan_directory(); QtWidgets.QMessageBox.information(self, "Success", "Sent to ComfyUI!")
        except Exception as e: QtWidgets.QMessageBox.critical(self, "Error", str(e))

# ================== EXPORT & INTEGRATION PIPELINE ==================

def export_clip(clip, export_folder, frame_range=None):
    cfg = ConfigManager()
    preset = cfg.get_preset_path(cfg.get('export_format', 'PNG 8-bit'))
    clip_name = sanitize_filename(_get_flame_attr_str(clip.name))
    seq_dir = os.path.join(export_folder, clip_name); os.makedirs(seq_dir, exist_ok=True)
    if flame:
        exporter = flame.PyExporter(); exporter.foreground = True
        try:
            if frame_range:
                exporter.export_between_marks = True
                dup = flame.duplicate(clip)
                try:
                    dup.name = clip.name
                    if frame_range == (0,0): ct = _safe_int_from_pytime(clip.current_time); dup.in_mark, dup.out_mark = ct, ct+1
                    else: dup.in_mark, dup.out_mark = frame_range[0], frame_range[1]+1
                    exporter.export(dup, preset, seq_dir)
                finally: flame.delete(dup)
            else: exporter.export(clip, preset, seq_dir)
            return seq_dir
        except Exception as e: log(f"Export Error: {e}"); return None
    return None

def wait_for_sequence(seq_dir, timeout=FILE_WAIT_TIMEOUT):
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(seq_dir):
            files = [f for f in os.listdir(seq_dir) if f.lower().endswith(('.png', '.exr', '.tif'))]
            if files:
                sz = sum(os.path.getsize(os.path.join(seq_dir, f)) for f in files)
                time.sleep(0.5)
                files2 = [f for f in os.listdir(seq_dir) if f.lower().endswith(('.png', '.exr', '.tif'))]
                sz2 = sum(os.path.getsize(os.path.join(seq_dir, f)) for f in files2)
                if len(files) == len(files2) and sz == sz2 and sz > 0: return seq_dir
        time.sleep(0.5)
    return None

def _get_flame_attr_str(attr):
    try: return (str(attr.get_value()) if hasattr(attr, 'get_value') else str(attr)).strip("'\"")
    except: return ''

def get_clip_from_item(item):
    if not flame: return None
    try:
        if hasattr(item, 'duration') and not hasattr(item, 'type'): return item
        for attr in ['clip', 'source', 'media', 'sequence', 'input']:
            if hasattr(item, attr):
                val = getattr(item, attr)
                if val and hasattr(val, 'duration'): return val
    except: pass
    return None

def _safe_int_from_pytime(val):
    try:
        if hasattr(val, 'get_value'): val = val.get_value()
        if hasattr(val, 'frame'): return int(val.frame)
        if hasattr(val, 'relative_frame'): return int(val.relative_frame)
        return int(val)
    except: return 1

# ================== FLAME MENU HOOKS ==================

_editor_window = None

def get_batch_custom_ui_actions():
    def open_settings(selection):
        dlg = ComfyUISettingsDialog()
        try: dlg.exec_()
        except: dlg.exec()

    def launch_editor(selection):
        cfg = ConfigManager()
        clips = [get_clip_from_item(i) for i in selection if get_clip_from_item(i)]
        clip_data_for_ui = []
        for clip in clips:
            clip_name = sanitize_filename(_get_flame_attr_str(clip.name))
            seq_dir = os.path.join(cfg.input_dir, clip_name)
            needs_export = True
            if os.path.exists(seq_dir) and any(f.lower().endswith(('.png', '.exr', '.tif', '.jpg')) for f in os.listdir(seq_dir)):
                msg = QtWidgets.QMessageBox(); msg.setStyleSheet(UIUtils.get_flame_stylesheet()); msg.setWindowTitle("Overwrite Export?")
                msg.setText(f"Clip '{clip_name}' has already been exported.\nDo you want to overwrite it?")
                msg.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel); msg.setDefaultButton(QtWidgets.QMessageBox.No)
                try: choice = msg.exec_()
                except: choice = msg.exec()
                if choice == QtWidgets.QMessageBox.Cancel: return
                elif choice == QtWidgets.QMessageBox.No: needs_export = False
            if needs_export:
                log(f"Exporting '{clip_name}'..."); exported_dir = export_clip(clip, cfg.input_dir)
                if exported_dir: wait_for_sequence(exported_dir)
            clip_data_for_ui.append((clip_name, seq_dir))
        global _editor_window; _editor_window = FlameBatchJsonEditor(selection, clip_data_for_ui)
        _editor_window.show()

    return [{'name': 'CO_ComfyUI', 'actions': [{'name': 'Send ComfyUI Job', 'execute': launch_editor}, {'name': 'Settings...', 'execute': open_settings, 'minimumVersion': '2025'}]}]

def initialize():
    cfg = ConfigManager(); log(f"ComfyUI Integration v{__version__} - LOADED")
    os.makedirs(cfg.input_dir, exist_ok=True); os.makedirs(cfg.workflows_dir, exist_ok=True)
    if cfg.get('auto_import'):
        try: start_watcher(cfg.output_dir, cfg.notification_file, cfg.pipeline_notification_file)
        except: pass

def app_initialized(project_name):
    try: initialize()
    except: pass

if flame and getattr(flame.project, 'current_project', None):
    try: initialize()
    except: pass