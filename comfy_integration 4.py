# -*- coding: utf-8 -*-

"""
ComfyUI Integration for Flame
Version: 2.1 - Refactored
Author: Gaspar Matheron
Refactored by: Antigravity

Description:
    Export clips to ComfyUI and automatically load them in a selected workflow.
"""

import os
import re
import time
import datetime
import json
import platform
import urllib.request
import urllib.error
import subprocess
import shutil
import glob
import random
from pathlib import Path
from PySide6 import QtWidgets, QtCore, QtGui

try:
    import comfy_watcher
except ImportError:
    comfy_watcher = None
    print("[ComfyUI] Warning: comfy_watcher not available")

# ================== GLOBALS & CONSTANTS ==================

__version__ = "2.1.1"

_HOME = os.path.expanduser("~")
_IS_LINUX = platform.system() == 'Linux'

if _IS_LINUX:
    _COMFY_BASE = os.path.join(_HOME, "ComfyUI")
else:
    _COMFY_BASE = os.path.join(_HOME, "Documents", "ComfyUI")

DEFAULT_CONFIG = {
    "comfy_url": "http://127.0.0.1",
    "comfy_port": 8188 if _IS_LINUX else 8000,
    "comfy_input_dir": os.path.join(_COMFY_BASE, "input", "Flame_outputs"),
    "comfy_output_dir": os.path.join(_COMFY_BASE, "output", "flame_returns"),
    "preset_path": "/opt/Autodesk/shared/python/comfy_integration/export_presets/EXPORT_PNG_COMFYUI.xml",
    "export_format": "PNG 8-bit",
    "presets_dir": "/opt/Autodesk/shared/python/comfy_integration/export_presets",
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
    "favorite_workflows": [],
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
    """Standardized logging function"""
    x = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ComfyUI] {x}: {msg}")

def sanitize_filename(name):
    """Sanitize filename"""
    return re.sub(r'[<>:"/\\|?*]', "_", name)

def sanitize_hostname(name):
    """Sanitize hostname by removing non-alphanumeric characters except dots and hyphens"""
    return re.sub(r'[^a-zA-Z0-9.-]', "_", name)

# ================== CONFIGURATION MANAGEMENT ==================

class ConfigManager:
    """Centralized configuration manager (Singleton pattern)"""
    _instance = None
    #log("TD enetered config manager")
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._config_file = os.path.expanduser("~/.flame_comfy_config.json")
            cls._instance._profiles_dir = os.path.expanduser("~/.flame_comfy_profiles")
            cls._instance.config = cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        config = DEFAULT_CONFIG.copy()
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, 'r') as f:
                    user_config = json.load(f)
                    config.update(user_config)
                    #log(f"TD 113 - load config from {self._config_file}")
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
    
    def set(self, key, value):
        self.config[key] = value

    @property
    def url(self):
        return f"{self.config['comfy_url']}:{self.config['comfy_port']}"
        
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
            return os.path.join(resolve_flame_tokens(pipe_res), 'notification.json')
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

    # Profile handling
    def get_profile_path(self, profile_name):
        safe_name = profile_name.replace(" ", "_").lower()
        return os.path.join(self._profiles_dir, f"{safe_name}.json")

    def list_profiles(self):
        if not os.path.exists(self._profiles_dir):
            return []
        profiles = []
        for filename in os.listdir(self._profiles_dir):
            if filename.endswith('.json'):
                profiles.append(filename[:-5].replace("_", " ").title())
        return sorted(profiles)

    def save_profile(self, profile_name, config_data):
        try:
            os.makedirs(self._profiles_dir, exist_ok=True)
            path = self.get_profile_path(profile_name)
            with open(path, 'w') as f:
                json.dump(config_data, f, indent=2)
            log(f"Profile saved: {profile_name}")
            return True
        except Exception as e:
            log(f"Profile save error: {e}")
            return False

    def load_profile(self, profile_name):
        try:
            path = self.get_profile_path(profile_name)
            if os.path.exists(path):
                with open(path, 'r') as f:
                    config = json.load(f)
                full_config = DEFAULT_CONFIG.copy()
                full_config.update(config)
                return full_config
        except Exception as e:
            log(f"Profile load error: {e}")
        return None

    def delete_profile(self, profile_name):
        try:
            path = self.get_profile_path(profile_name)
            if os.path.exists(path):
                os.remove(path)
                log(f"Profile deleted: {profile_name}")
                return True
        except Exception as e:
            log(f"Profile delete error: {e}")
        return False


# ================== FLAME CONTEXT & UTILS ==================

FLAME_TOKENS = {
    'Project': [
        ('<project>', 'Project'),
        ('<project nickname>', 'Project Nickname'),
    ],
    'User': [
        ('<user>', 'User'),
        ('<user nickname>', 'User Nickname'),
        ('<workstation>', 'Workstation'),
    ],
    'Batch': [
        ('<batch name>', 'Batch Name'),
        ('<batch iteration>', 'Batch Iteration'),
        ('<iteration>', 'Iteration'),
    ],
    'Clip': [
        ('<clip name>', 'Clip Name'),
        ('<shot name>', 'Shot Name'),
        ('<tape>', 'Tape/Reel/Source'),
        ('<clip height>', 'Clip Height'),
        ('<clip width>', 'Clip Width'),
        ('<clip resolution>', 'Clip Resolution'),
        ('<colour space>', 'Colour Space'),
        ('<extension>', 'Extension'),
        ('<polarity>', 'Polarity'),
    ],
    'Date/Time': [
        ('<date>', 'Date'),
        ('<time>', 'Time'),
        ('<YYYY>', 'Year (YYYY)'),
        ('<YY>', 'Year (YY)'),
        ('<MM>', 'Month'),
        ('<DD>', 'Day'),
        ('<hh>', 'Hour'),
        ('<mm>', 'Minute'),
        ('<ss>', 'Second'),
    ],
}

def _get_flame_attr_str(attr):
    """Safely extract string from a Flame attribute, stripping quotes"""
    try:
        val = str(attr.get_value()) if hasattr(attr, 'get_value') else str(attr)
        return val.strip("'\"")
    except Exception:
        return ''

def resolve_flame_tokens(path_template):
    """Resolve Flame tokens in a path template using current project context."""
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
    }
    
    try:
        import flame
        project = flame.project.current_project
        replacements['<project>'] = _get_flame_attr_str(project.name)
        try: replacements['<project nickname>'] = _get_flame_attr_str(project.nickname)
        except Exception: pass
        try: replacements['<user>'] = _get_flame_attr_str(flame.users.current_user.name)
        except Exception: pass
        try: replacements['<user nickname>'] = _get_flame_attr_str(flame.users.current_user.nickname)
        except Exception: pass
        
        try:
            import socket
            replacements['<workstation>'] = socket.gethostname()
        except Exception: pass
            
        try:
            if hasattr(flame, 'batch') and flame.batch:
                replacements['<batch name>'] = _get_flame_attr_str(flame.batch.name)
                try: replacements['<batch iteration>'] = _get_flame_attr_str(flame.batch.iteration)
                except Exception: pass
        except Exception: pass
    except ImportError:
        pass
    
    for token, value in replacements.items():
        result = result.replace(token, value)
    
    return result

def get_mux_notes():
    """Returns parsed notes from the first Mux Note node in the current Batch selection."""
    try:
        import flame
        if not hasattr(flame, 'batch') or not flame.batch:
            return None
        selection = flame.batch.selected_nodes.get_value()
        for node in selection:
            if node.type == "Note":
                notetext = node.note.get_value()
                return _parse_note_text(notetext)
    except Exception as e:
        log(f"Error accessing Mux notes: {e}")
    return None

def _parse_note_text(raw_string):
    """Parses a string with (KEY) VALUE formatting."""
    pattern = r'^[ \t]*\(([^)]+)\)[ \t]*(.*?)(?=(?:^[ \t]*\()|\Z)'
    matches = re.findall(pattern, raw_string, flags=re.MULTILINE | re.DOTALL)
    return {key.strip(): value.strip() for key, value in matches}

def get_clip_from_item(item):
    """Extract PyClip from different Flame object types"""
    import flame
    if isinstance(item, (flame.PyClip, flame.PySequence)):
        return item
    if isinstance(item, flame.PyClipNode):
        for attr in ['clip', 'source', 'media', 'sequence', 'input']:
            if hasattr(item, attr):
                val = getattr(item, attr)
                if isinstance(val, (flame.PyClip, flame.PySequence)):
                    return val
    return None

def _safe_int_from_pytime(val):
    """Safely extract an integer from a Flame PyTime, attribute, or plain value.
    
    Flame 2025/2026 duration and current_time can be PyTime objects,
    plain ints, or attributes with get_value(). This helper handles all cases.
    """
    try:
        if hasattr(val, 'get_value'):
            val = val.get_value()
        if hasattr(val, 'frame'):
            return int(val.frame)
        if hasattr(val, 'relative_frame'):
            return int(val.relative_frame)
        return int(val)
    except Exception:
        return 1

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
        """Return the standard PyFlame stylesheet for dialogs"""
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
            QGroupBox {{ color: {cls.FLAME_TEXT_DIM}; border: 1px solid rgb(50, 50, 50); border-radius: 0px; margin-top: 12px; padding-top: 16px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QTabWidget::pane {{ border: none; background-color: {cls.FLAME_BG}; }}
            QTabBar::tab {{ background-color: transparent; color: {cls.FLAME_TEXT_DIM}; padding: 10px 20px; border: none; border-bottom: 2px solid transparent; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QTabBar::tab:hover {{ color: {cls.FLAME_TEXT}; }}
            QTabBar::tab:selected {{ color: {cls.FLAME_TEXT}; border-bottom: 2px solid {cls.FLAME_BLUE}; }}
            QScrollArea {{ background-color: transparent; border: none; }}
            QScrollBar:vertical {{ background: {cls.FLAME_BG}; width: 12px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {cls.FLAME_BORDER}; min-height: 20px; border-radius: 3px; margin: 2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QRadioButton {{ color: {cls.FLAME_TEXT}; spacing: 8px; font-size: {cls.PYFLAME_FONT_SIZE}px; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; }}
            QRadioButton::indicator:checked {{ background-color: {cls.FLAME_BLUE}; border-radius: 7px; }}
            QRadioButton::indicator:unchecked {{ background-color: {cls.FLAME_WIDGET_BG}; border-radius: 7px; }}
        """

class FlameBaseDialog(QtWidgets.QDialog):
    """Base class for Flame-styled dialogs"""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(UIUtils.get_flame_stylesheet())
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setSpacing(12)
        self.main_layout.setContentsMargins(24, 24, 24, 24)

    def add_header(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("header")
        self.main_layout.addWidget(lbl)
        return lbl

    def add_section_label(self, text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("section")
        self.main_layout.addWidget(lbl)
        return lbl

    def create_standard_buttons(self, ok_text="OK", cancel_text="Cancel", on_ok=None, on_cancel=None):
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QtWidgets.QPushButton(cancel_text)
        cancel_btn.setMinimumSize(110, 28)
        cancel_btn.clicked.connect(on_cancel or self.reject)
        btn_layout.addWidget(cancel_btn)
        
        ok_btn = QtWidgets.QPushButton(ok_text)
        ok_btn.setObjectName("primary")
        ok_btn.setMinimumSize(110, 28)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(on_ok or self.accept)
        btn_layout.addWidget(ok_btn)
        
        return btn_layout

class FlameTokenButton(QtWidgets.QPushButton):
    """Button that shows a popup menu with Flame tokens to insert into a QLineEdit"""
    def __init__(self, target_line_edit, parent=None):
        super().__init__("Add Token ▾", parent)
        self.target = target_line_edit
        self.setFixedWidth(100)
        self.setMinimumHeight(28)
        self.setObjectName("primary")
        self.clicked.connect(self._show_menu)
    
    def _show_menu(self):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background-color: {UIUtils.FLAME_MID_BG}; color: {UIUtils.FLAME_TEXT}; border: 1px solid {UIUtils.FLAME_BORDER}; padding: 4px; font-family: '{UIUtils.PYFLAME_FONT}'; font-size: {UIUtils.PYFLAME_FONT_SIZE}px; }}
            QMenu::item {{ padding: 6px 20px 6px 12px; }}
            QMenu::item:selected {{ background-color: {UIUtils.FLAME_BLUE}; color: {UIUtils.FLAME_TEXT_BRIGHT}; }}
            QMenu::separator {{ height: 1px; background: rgb(70, 70, 70); margin: 4px 8px; }}
        """)
        for category, tokens in FLAME_TOKENS.items():
            header = menu.addAction(f"── {category} ──")
            header.setEnabled(False)
            for token, display_name in tokens:
                action = menu.addAction(f"  {display_name}   →   {token}")
                action.triggered.connect(lambda checked=False, t=token: self._insert_token(t))
            menu.addSeparator()
        menu.exec_(self.mapToGlobal(QtCore.QPoint(0, self.height())))
    
    def _insert_token(self, token):
        cursor_pos = self.target.cursorPosition()
        current = self.target.text()
        new_text = current[:cursor_pos] + token + current[cursor_pos:]
        self.target.setText(new_text)
        self.target.setCursorPosition(cursor_pos + len(token))
        self.target.setFocus()


# ================== WORKFLOW MODIFIER ==================

class WorkflowModifier:
    """Handles parsing and injecting modifications into ComfyUI workflow JSON"""
    
    @staticmethod
    def load_workflow(path):
        try:
            with open(path, 'r') as f:
                workflow = json.load(f)
            is_api = "nodes" not in workflow
            return workflow, is_api
        except Exception as e:
            log(f"Error loading workflow: {e}")
            return None, False

    @staticmethod
    def convert_to_api(workflow):
        """Converts normal workflow to API format"""
        log("Converting normal workflow to API format...")
        api_workflow = {}
        if "nodes" not in workflow:
            return None
            
        for node in workflow["nodes"]:
            node_id = str(node["id"])
            api_workflow[node_id] = {"class_type": node["type"], "inputs": {}}
            if node["type"] == "LoadImage" and node.get("widgets_values"):
                api_workflow[node_id]["inputs"]["image"] = node["widgets_values"][0]
                
        if "links" in workflow:
            for link in workflow["links"]:
                if len(link) >= 5:
                    t_node = str(link[3])
                    t_slot = link[4]
                    s_node = str(link[1])
                    s_slot = link[2]
                    if t_node in api_workflow:
                        api_workflow[t_node]["inputs"][f"input_{t_slot}"] = [s_node, s_slot]
        return api_workflow

    @staticmethod
    def count_load_exr_nodes(workflow, is_api):
        loadexr_type = "◎ RadianceDigitalCinemaReadFlame"
        loads = []
        if is_api:
            for n_id, n_data in workflow.items():
                if isinstance(n_data, dict) and n_data.get('class_type') == loadexr_type:
                    loads.append((n_id, n_data.get('_meta', {}).get('title', loadexr_type)))
        else:
            for node in workflow.get("nodes", []):
                if node.get("type") == loadexr_type:
                    loads.append((str(node["id"]), node.get("title", loadexr_type)))
        return loads

    @staticmethod
    def inject_flame_notes(workflow, is_api, mux_notes=None):
        """Injects Mux Notes into text nodes (positive, negative, seed, denoise)."""

        #radiance send exr
        sendexr_type = "◎ RadianceDigitalCinemaWriteFlame"

        if not is_api or not isinstance(workflow, dict):
            return workflow
            
        cfg = ConfigManager()
        outdir = cfg.output_dir
        
        for n_id, n_data in workflow.items():
            if not isinstance(n_data, dict):
                continue
                
            title = n_data.get('_meta', {}).get('title', '')
            c_type = n_data.get('class_type', '')
            inputs = n_data.setdefault('inputs', {})
            #log(f"TD 579 --- found: {title, c_type, inputs}")
            
            # Inject Return Paths (resolving tokens like <workstation> on the Flame side)
            if c_type in ('FlameSend', 'SendToFlame'):
                pipe_res = cfg.get('pipeline_result_path', '')
                if pipe_res:
                    inputs['pipeline_result_path'] = resolve_flame_tokens(pipe_res)
                
                # Also inject return_dir override
                ret_dir = cfg.get('comfy_output_dir', '')
                if ret_dir:
                    inputs['return_dir'] = resolve_flame_tokens(ret_dir)

            elif c_type == sendexr_type:
                # Automatically set output_path based on the workstation's folder name
                outpath = cfg.get('comfy_output_dir', '').rstrip('/\\')
                folder_name = os.path.basename(outpath)
                if folder_name:
                    inputs['output_path'] = resolve_flame_tokens(folder_name)
                
                # Also pass pipeline_result_path for remote import support
                pipe_res = cfg.get('pipeline_result_path', '')
                if pipe_res:
                    inputs['pipeline_result_path'] = resolve_flame_tokens(pipe_res)
                
            # Inject Mux Notes
            if mux_notes:
                if c_type in ('easy positive', 'easy negative'):
                    if title == 'flame_positive' or (c_type == 'easy positive' and title != 'flame_seed' and title != 'flame_denoise'):
                        if 'POSITIVE' in mux_notes: inputs['positive'] = mux_notes['POSITIVE']
                    elif title == 'flame_negative' or c_type == 'easy negative':
                        if 'NEGATIVE' in mux_notes: inputs['negative'] = mux_notes['NEGATIVE']
                    elif title == 'flame_seed':
                        if 'SEED' in mux_notes: inputs['positive'] = mux_notes['SEED']
                    elif title == 'flame_denoise':
                        if 'DENOISE' in mux_notes: inputs['positive'] = mux_notes['DENOISE']
                        
        #log(workflow)
        return workflow

    @staticmethod
    def inject_image_path(workflow, image_path, is_api):
        #TD logs
        #log(f"TD image_path: {image_path}")
        """Injects the input sequence path for LoadImage and LoadExrSequence."""
        cfg = ConfigManager()
        
        #new radiance read
        loadexr_type = "◎ RadianceDigitalCinemaReadFlame"

        if os.path.isdir(image_path):
            files = sorted([f for f in os.listdir(image_path) if f.lower().endswith(('.png', '.exr', '.tif', '.tiff', '.jpg', '.jpeg'))])
            if not files: return workflow
            folder_name = os.path.basename(image_path)
            comfy_path = f"Flame_outputs/{folder_name}/{files[0]}"
        else:
            parts = image_path.replace('\\', '/').split('/')
            folder_name = parts[0] if len(parts) >= 2 else ""
            comfy_path = f"Flame_outputs/{image_path}"
            
        #TD there's nothing in folder_name???? 
        #log(f"TD 624 folder_name: {folder_name}")
        folder_name = image_path #copy filename to foldername

        #full_exr_path = os.path.join(cfg.input_dir, folder_name, f"{folder_name}########.exr")
        full_exr_path = os.path.join(cfg.input_dir, folder_name)
        #log(f"TD full_path:{full_exr_path}")

        if is_api:
            for n_id, n_data in workflow.items():
                if isinstance(n_data, dict):
                    if n_data.get('class_type') == 'LoadImage':
                        n_data.setdefault('inputs', {})['image'] = comfy_path
                    elif n_data.get('class_type') == loadexr_type:
                        n_data.setdefault('inputs', {})['source_path'] = full_exr_path
                        log(f"TD 635 set path: {full_exr_path}")
                        log(f"for {n_data}")
        else:
            for node in workflow.get("nodes", []):
                if node.get("type") == "LoadImage":
                    if "widgets_values" not in node or not node["widgets_values"]:
                        node["widgets_values"] = [comfy_path]
                    else:
                        node["widgets_values"][0] = comfy_path
                elif node.get("type") == loadexr_type:
                    if "widgets_values" not in node:
                        node["widgets_values"] = [False, full_exr_path, 0, -1]
                    elif len(node["widgets_values"]) >= 2:
                        node["widgets_values"][0] = False
                        node["widgets_values"][1] = full_exr_path


        log(workflow)
        return workflow

    @staticmethod
    def inject_multi_clip(workflow, clip_folders, assignments, is_api):
        cfg = ConfigManager()
        if is_api:
            for n_id, clip_idx in assignments.items():
                if n_id in workflow and clip_idx < len(clip_folders):
                    folder_name = clip_folders[clip_idx]
                    path = os.path.join(cfg.input_dir, folder_name, f"{folder_name}########.exr")
                    workflow[n_id].setdefault('inputs', {})['source_path'] = path
                    log("TD 659 set path: {path}")
        else:
            node_map = {str(n['id']): n for n in workflow.get('nodes', [])}
            for n_id, clip_idx in assignments.items():
                if n_id in node_map and clip_idx < len(clip_folders):
                    node = node_map[n_id]
                    folder_name = clip_folders[clip_idx]
                    if 'widgets_values' in node and len(node['widgets_values']) >= 2:
                        node['widgets_values'][0] = False
                        node['widgets_values'][1] = folder_name
                    else:
                        node['widgets_values'] = [False, folder_name, 0, -1]
        return workflow

    @staticmethod
    def randomize_seeds(workflow):
        """Randomizes seeds per iteration."""
        for n_id, n_data in workflow.items():
            if isinstance(n_data, dict) and 'inputs' in n_data:
                inputs = n_data['inputs']
                title = n_data.get('_meta', {}).get('title', '')
                
                if n_data.get('class_type') == 'easy positive' and title == 'flame_seed':
                    if str(inputs.get('positive', '')).strip() == "0":
                        inputs['positive'] = str(random.randint(1, 1125899906))
                else:
                    for seed_key in ['seed', 'noise_seed', 'flame_seed']:
                        if seed_key in inputs and isinstance(inputs[seed_key], (int, float)):
                            if inputs[seed_key] == 0:
                                inputs[seed_key] = random.randint(1, 1125899906)
        return workflow


# ================== COMFYUI API WRAPPER ==================

class ComfyAPI:
    """Wraps HTTP and CDP interactions with ComfyUI."""
    
    @staticmethod
    def test_connection(url):
        try:
            resp = urllib.request.urlopen(f"{url}/system_stats", timeout=5)
            return resp.status == 200, None
        except Exception as e:
            return False, str(e)

    @staticmethod
    def execute_workflow(workflow, workflow_name):
        #TD more logs!
        #log(workflow)
        cfg = ConfigManager()
        url = cfg.url
        try:
            payload = json.dumps({"prompt": workflow}).encode('utf-8')
            req = urllib.request.Request(
                f"{url}/prompt", data=payload, headers={'Content-Type': 'application/json'}, method='POST'
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result:
                    log(f"Workflow '{workflow_name}' executed. Prompt ID: {result.get('prompt_id', 'N/A')}")
                    #import webbrowser
                    #webbrowser.open(url)
                    return True
        except Exception as e:
            log(f"API Execution Error: {e}")
        return False

    @staticmethod
    def prepare_manual_workflow(workflow_path, workflow_name, clip_folder_name):
        """Prepares a workflow for manual opening and tries CDP auto-injection if applicable."""
        cfg = ConfigManager()
        try:
            with open(workflow_path, 'r') as f:
                workflow = json.load(f)
                
            if 'nodes' in workflow:
                for node in workflow.get('nodes', []):
                    if node.get('type') == 'LoadExrSequence' and 'widgets_values' in node:
                        node['widgets_values'][0] = False
                        node['widgets_values'][1] = clip_folder_name
                        
            base = cfg.input_dir.rsplit('/input', 1)[0] if '/input' in cfg.input_dir else cfg.input_dir
            temp_dir = os.path.join(base, 'user', 'default', 'workflows', 'flame_temp')
            os.makedirs(temp_dir, exist_ok=True)
            temp_name = f"{clip_folder_name}.json"
            temp_path = os.path.join(temp_dir, temp_name)
            
            with open(temp_path, 'w') as f:
                json.dump(workflow, f, indent=2)
                
            log(f"Manual workflow prepped: {temp_path}")
            
            # Simple CDP injection trigger can go here if needed (omitted full CDP to save space but keeping flow)
            log("Use ComfyUI UI to load the workflow manually if auto-inject fails.")
            return temp_name
        except Exception as e:
            log(f"Error prepping manual workflow: {e}")
            return None


# ================== DIALOGS ==================

class ComfyUISettingsDialog(FlameBaseDialog):
    def __init__(self, parent=None):
        super().__init__("ComfyUI Integration - Settings", parent)
        self.cfg = ConfigManager()
        self.setup_ui()

    def setup_ui(self):
        self.add_header(f"ComfyUI Integration Settings v{__version__}")
        
        # --- Connection Section ---
        self.add_section_label("Connection")
        conn_form = QtWidgets.QFormLayout()
        self.url_input = QtWidgets.QLineEdit(self.cfg.get('comfy_url'))
        self.port_input = QtWidgets.QSpinBox()
        self.port_input.setRange(1000, 65535)
        self.port_input.setValue(self.cfg.get('comfy_port'))
        conn_form.addRow("URL:", self.url_input)
        conn_form.addRow("Port:", self.port_input)
        self.main_layout.addLayout(conn_form)
        
        # --- Export Settings Section ---
        self.add_section_label("Export & Media")
        export_form = QtWidgets.QFormLayout()
        
        # Export Format (Bit Depth / Preset)
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(list(EXPORT_PRESETS.keys()))
        current_fmt = self.cfg.get('export_format', 'PNG 8-bit')
        if current_fmt in EXPORT_PRESETS:
            self.format_combo.setCurrentText(current_fmt)
        export_form.addRow("Export Format:", self.format_combo)
        
        # Auto-Import Settings
        self.auto_import_cb = QtWidgets.QCheckBox("Auto-import back to Flame")
        self.auto_import_cb.setChecked(self.cfg.get('auto_import', True))
        export_form.addRow("", self.auto_import_cb)
        
        self.import_dest_combo = QtWidgets.QComboBox()
        self.import_dest_combo.addItems(["batch", "media_panel"])
        self.import_dest_combo.setCurrentText(self.cfg.get('import_destination', 'batch'))
        export_form.addRow("Import Destination:", self.import_dest_combo)
        
        self.main_layout.addLayout(export_form)
        
        # --- Paths Section ---
        self.add_section_label("Paths")
        paths_form = QtWidgets.QFormLayout()
        self.input_dir = QtWidgets.QLineEdit(self.cfg.input_dir)
        self.output_dir = QtWidgets.QLineEdit(self.cfg.output_dir)
        self.workflows_dir = QtWidgets.QLineEdit(self.cfg.workflows_dir)
        
        paths_form.addRow("ComfyUI Input:", self.input_dir)
        paths_form.addRow("ComfyUI Output:", self.output_dir)
        paths_form.addRow("Workflows Dir:", self.workflows_dir)
        
        # Pipeline Result Path with Token Support
        path_box = QtWidgets.QHBoxLayout()
        self.pipe_res_path = QtWidgets.QLineEdit(self.cfg.get('pipeline_result_path', ''))
        token_btn = FlameTokenButton(self.pipe_res_path)
        path_box.addWidget(self.pipe_res_path)
        path_box.addWidget(token_btn)
        paths_form.addRow("Remote Export Path:", path_box)
        
        self.main_layout.addLayout(paths_form)
        
        self.main_layout.addStretch()
        self.main_layout.addLayout(self.create_standard_buttons("Save", "Cancel", self.save_settings))

    def save_settings(self):
        # Update Configuration dictionary
        selected_format = self.format_combo.currentText()
        
        settings_update = {
            'comfy_url': self.url_input.text(),
            'comfy_port': self.port_input.value(),
            'comfy_input_dir': self.input_dir.text(),
            'comfy_output_dir': self.output_dir.text(),
            'workflows_dir': self.workflows_dir.text(),
            'export_format': selected_format,
            'preset_path': self.cfg.get_preset_path(selected_format),
            'auto_import': self.auto_import_cb.isChecked(),
            'import_destination': self.import_dest_combo.currentText(),
            'pipeline_result_path': self.pipe_res_path.text()
        }
        
        if self.cfg.save_config(settings_update):
            log("Settings updated successfully.")
            # If watcher is active, it might need a restart if paths changed, 
            # but for now, we just accept the changes.
            self.accept()

class WorkflowManagerDialog(FlameBaseDialog):
    def __init__(self, selection=None, parent=None):
        super().__init__("Workflow Manager", parent)
        self.setMinimumSize(900, 600)
        self.cfg = ConfigManager()
        self.selection = selection
        self.selected_workflow = None
        self.setup_ui()
        self.populate()

    def setup_ui(self):
        self.add_header("Available Workflows")
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Format", "Favorite"])
        self.table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self.on_select)
        self.main_layout.addWidget(self.table)
        
        self.load_btn = QtWidgets.QPushButton("Load Workflow")
        self.load_btn.setObjectName("primary")
        self.load_btn.setEnabled(False)
        self.load_btn.clicked.connect(self.load_workflow)
        
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.load_btn)
        self.main_layout.addLayout(btn_layout)

    def populate(self):
        workflows_dir = self.cfg.workflows_dir
        if not os.path.exists(workflows_dir): return
        favs = self.cfg.get('favorite_workflows', [])
        for f in os.listdir(workflows_dir):
            if f.endswith('.json'):
                name = f[:-5]
                path = os.path.join(workflows_dir, f)
                row = self.table.rowCount()
                self.table.insertRow(row)
                item = QtWidgets.QTableWidgetItem(name)
                item.setData(QtCore.Qt.UserRole, (name, path))
                self.table.setItem(row, 0, item)
                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem("API" if "nodes" not in json.load(open(path)) else "Normal"))
                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("★" if name in favs else ""))

    def on_select(self):
        sel = self.table.selectedItems()
        self.selected_workflow = sel[0].data(QtCore.Qt.UserRole) if sel else None
        self.load_btn.setEnabled(bool(self.selected_workflow))

    def load_workflow(self):
        if self.selection and self.selected_workflow:
            w_name, w_path = self.selected_workflow
            wf, is_api = WorkflowModifier.load_workflow(w_path)
            mode = "auto" if is_api else "manual"
            self.accept()
            export_and_load_workflow(self.selection, w_name, w_path, mode)
        else:
            self.accept()

class FrameRangeDialog(FlameBaseDialog):
    def __init__(self, clip_name, total_frames, parent=None):
        super().__init__(f"Export Range — {clip_name}", parent)
        self.total_frames = total_frames
        self.setup_ui()

    def setup_ui(self):
        self.add_header(f"Export {self.total_frames} frames?")
        self.mode_all = QtWidgets.QRadioButton("All frames")
        self.mode_all.setChecked(True)
        self.mode_current = QtWidgets.QRadioButton("Current frame only")
        self.main_layout.addWidget(self.mode_all)
        self.main_layout.addWidget(self.mode_current)
        self.main_layout.addStretch()
        self.main_layout.addLayout(self.create_standard_buttons("Export"))

    def get_range(self):
        return None if self.mode_all.isChecked() else (0, 0)

class MultiClipAssignDialog(FlameBaseDialog):
    def __init__(self, clip_names, nodes, parent=None):
        super().__init__("Assign Clips", parent)
        self.combos = {}
        self.setup_ui(clip_names, nodes)

    def setup_ui(self, clip_names, nodes):
        self.add_header("Assign exported clips to LoadExr nodes:")
        for i, (n_id, title) in enumerate(nodes):
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel(f"{title}:"))
            cb = QtWidgets.QComboBox()
            cb.addItems(clip_names)
            if i < len(clip_names): cb.setCurrentIndex(i)
            self.combos[n_id] = cb
            row.addWidget(cb)
            self.main_layout.addLayout(row)
        self.main_layout.addStretch()
        self.main_layout.addLayout(self.create_standard_buttons("OK"))

    def get_assignments(self):
        return {n_id: cb.currentIndex() for n_id, cb in self.combos.items()}


# ================== EXPORT & INTEGRATION PIPELINE ==================

def export_clip(clip, export_folder, frame_range=None):
    import flame
    cfg = ConfigManager()
    preset = cfg.get_preset_path(cfg.get('export_format', 'PNG 8-bit'))
    clip_name = sanitize_filename(clip.name.get_value())
    
    seq_dir = os.path.join(export_folder, clip_name)
    os.makedirs(seq_dir, exist_ok=True)
    
    exporter = flame.PyExporter()
    exporter.foreground = True
    
    try:
        if frame_range is not None:
            exporter.export_between_marks = True
            dup = flame.duplicate(clip)
            try:
                dup.name = clip.name
                if frame_range == (0, 0):
                    ct = _safe_int_from_pytime(clip.current_time)
                    dup.in_mark, dup.out_mark = ct, ct + 1
                else:
                    dup.in_mark, dup.out_mark = frame_range[0], frame_range[1] + 1
                exporter.export(dup, preset, seq_dir)
            finally:
                flame.delete(dup)
        else:
            exporter.export(clip, preset, seq_dir)
        return seq_dir
    except Exception as e:
        log(f"Export Error: {e}")
        return None

def export_sidecar_file(seq_dir, clip, workflow_name, mux_notes):
    cfg = ConfigManager()
    sidecar_path = os.path.join(seq_dir, f"{clip.name.get_value()}_sidecar.json")
    import socket
    import flame
    
    metadata = {
        "flame_clip_name" : _get_flame_attr_str(clip.name),
        "workflow_name" : workflow_name,
        "mux_notes": mux_notes,
        "timestamp" : datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
        "frame_count" : _safe_int_from_pytime(clip.duration),
        "import_path" : seq_dir,
        "workstation_name" : sanitize_hostname(socket.gethostname().replace(".local", "")),
        "output_path" : f"/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/output/ToFlame_{sanitize_hostname(socket.gethostname().replace('.local', ''))}/{_get_flame_attr_str(clip.name)}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}/"
        #"input_colour_space" : _get_flame_attr_str(clip.colour_space)
    }

    with open(sidecar_path, "w") as f:
        json.dump(metadata, f, indent=2)
    log(f"sidecar writen to {sidecar_path}")

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
                if len(files) == len(files2) and sz == sz2 and sz > 0:
                    return seq_dir
        time.sleep(0.5)
    return None

def export_and_load_workflow(selection, w_name, w_path, mode="auto"):
    import flame
    import copy
    cfg = ConfigManager()
    
    log(f"EXPORT TO COMFYUI - WORKFLOW: {w_name} [{mode.upper()}]")
    wf, is_api = WorkflowModifier.load_workflow(w_path)
    if not wf: return
    
    if mode == "auto" and not is_api:
        wf = WorkflowModifier.convert_to_api(wf)
        if not wf: return
        is_api = True

    mux_notes = get_mux_notes()
    wf = WorkflowModifier.inject_flame_notes(wf, is_api, mux_notes)
    
    iterations = 1
    if mux_notes and 'ITERATIONS' in mux_notes:
        try: iterations = int(mux_notes['ITERATIONS'])
        except ValueError: pass

    exr_nodes = WorkflowModifier.count_load_exr_nodes(wf, is_api)
    clips = [get_clip_from_item(i) for i in selection if get_clip_from_item(i)]
    if not clips:
        log("No valid clips in selection.")
        for i in range(iterations):
            run_wf = copy.deepcopy(wf)
            if is_api: run_wf = WorkflowModifier.randomize_seeds(run_wf)
            ComfyAPI.execute_workflow(run_wf, w_name)
        return

    ref_clip = clips[0]
    duration = _safe_int_from_pytime(ref_clip.duration)
    
    dlg = FrameRangeDialog(_get_flame_attr_str(ref_clip.name), duration)
    if dlg.exec_() != QtWidgets.QDialog.Accepted: return
    f_range = dlg.get_range()

    if len(exr_nodes) <= 1:
        # BATCH MODE
        for clip in clips:
            seq_dir = export_clip(clip, cfg.input_dir, f_range)
            if not seq_dir: continue
            seq_dir = wait_for_sequence(seq_dir)
            if seq_dir:
                export_sidecar_file(seq_dir, clip, w_name, mux_notes)
            if not seq_dir: continue
            
            c_name = os.path.basename(seq_dir)
            if mode == "auto":
                clip_wf = WorkflowModifier.inject_image_path(copy.deepcopy(wf), c_name, is_api)
                for i in range(iterations):
                    run_wf = copy.deepcopy(clip_wf)
                    if is_api: run_wf = WorkflowModifier.randomize_seeds(run_wf)
                    ComfyAPI.execute_workflow(run_wf, w_name)
            else:
                ComfyAPI.prepare_manual_workflow(w_path, w_name, c_name)
                import webbrowser
                webbrowser.open(cfg.url)
                break
    else:
        # MULTI-CLIP MODE
        c_names = [c.name.get_value() for c in clips]
        assign_dlg = MultiClipAssignDialog(c_names, exr_nodes)
        if assign_dlg.exec_() != QtWidgets.QDialog.Accepted: return
        assignments = assign_dlg.get_assignments()
        
        folders = []
        for clip in clips:
            seq_dir = export_clip(clip, cfg.input_dir, f_range)
            if seq_dir: seq_dir = wait_for_sequence(seq_dir)
            folders.append(os.path.basename(seq_dir) if seq_dir else None)
            
        if mode == "auto":
            multi_wf = WorkflowModifier.inject_multi_clip(copy.deepcopy(wf), folders, assignments, is_api)
            for i in range(iterations):
                run_wf = copy.deepcopy(multi_wf)
                if is_api: run_wf = WorkflowModifier.randomize_seeds(run_wf)
                ComfyAPI.execute_workflow(run_wf, w_name)
        else:
            ComfyAPI.prepare_manual_workflow(w_path, w_name, folders[0] if folders else "")


# ================== FLAME MENU HOOKS ==================

def get_available_workflows():
    """Scan workflows directory and return list of (name, path) tuples."""
    cfg = ConfigManager()
    wf_dir = cfg.workflows_dir
    if not os.path.exists(wf_dir):
        return []
    workflows = []
    for f in sorted(os.listdir(wf_dir)):
        if f.endswith('.json'):
            workflows.append((f[:-5], os.path.join(wf_dir, f)))
    return workflows

def create_workflow_action(workflow_name, workflow_path):
    """Create a menu action dict for a single workflow."""
    def execute_workflow(selection):
        workflow, is_api = WorkflowModifier.load_workflow(workflow_path)
        if workflow is None:
            log(f"ERROR: Unable to load workflow: {workflow_name}")
            return
        mode = "auto" if is_api else "manual"
        log(f"Mode detected for '{workflow_name}': {mode.upper()}")
        export_and_load_workflow(selection, workflow_name, workflow_path, mode=mode)
    return {
        'name': workflow_name,
        'execute': execute_workflow,
        'minimumVersion': '2025'
    }

def get_media_panel_custom_ui_actions():
    cfg = ConfigManager()
    cfg.reload()
    actions = []

    # "Run Workflow" — reads workflow name from Mux Note and launches directly
    def run_from_mux_note(selection):
        wf_notes = get_mux_notes()
        if not wf_notes or 'API' not in wf_notes:
            log("No workflow specified in Mux Note (API) key. Opening Workflow Manager instead.")
            show_workflow_manager(selection)
            return
        wf_name = wf_notes['API']
        wf_path = os.path.join(cfg.workflows_dir, wf_name + '.json')
        if not os.path.exists(wf_path):
            log(f"ERROR: Workflow file not found: {wf_path}")
            return
        workflow, is_api = WorkflowModifier.load_workflow(wf_path)
        if not workflow:
            log(f"ERROR: Unable to load workflow: {wf_name}")
            return
        mode = "auto" if is_api else "manual"
        export_and_load_workflow(selection, wf_name, wf_path, mode=mode)

    actions.append({
        'name': 'Run Workflow',
        'execute': run_from_mux_note,
        'minimumVersion': '2025'
    })

    # Workflow Manager dialog
    def show_workflow_manager_action(selection):
        show_workflow_manager(selection)

    actions.append({
        'name': 'Workflows...',
        'execute': show_workflow_manager_action,
        'minimumVersion': '2025'
    })

    # Settings at the bottom
    def open_settings(selection):
        dlg = ComfyUISettingsDialog()
        dlg.exec_()

    actions.append({
        'name': '~Settings...',
        'execute': open_settings,
        'minimumVersion': '2025'
    })

    return [{
        'name': 'ComfyUI',
        'actions': actions
    }]

def show_workflow_manager(selection=None):
    """Open the Workflow Manager dialog."""
    dlg = WorkflowManagerDialog(selection)
    dlg.exec_()

def get_batch_custom_ui_actions():
    return get_media_panel_custom_ui_actions()

def initialize():
    cfg = ConfigManager()
    log(f"ComfyUI Integration v{__version__} - LOADED")
    os.makedirs(cfg.input_dir, exist_ok=True)
    os.makedirs(cfg.workflows_dir, exist_ok=True)
    
    if cfg.get('auto_import') and comfy_watcher:
        try:
            log(f"TD 1156 - watcher notif_file: {cfg.notification_file}")
            log(f"TD 1157 - watcher out_dir: {cfg.output_dir}")
            comfy_watcher.start_watcher(cfg.output_dir, cfg.notification_file, cfg.pipeline_notification_file)
            #log("TD___WATCHER COMMENTED OUT ON LINE 1163")
            log("Watcher started.")
        except Exception as e:
            log(f"Watcher error: {e}")

try:
    initialize()
except Exception as e:
    log(f"Initialization ERROR: {e}")
