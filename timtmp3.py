from __future__ import print_function
import json
import os
import datetime
import re
import flame

# Flame supports PySide6 in newer versions, PySide2 in older ones
try:
    from PySide6 import QtWidgets, QtCore, QtGui
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui

# Updated persistent storage path for ComfyUI profiles
DIRECTORY_PATH = "/01_OUTPOST_STORE/01_OUTPOST/02_POST/ComfyUI/workflows/CO_FlameIntegrations/CO_Profiles"

class FlameBatchJsonEditor(QtWidgets.QDialog):
    def __init__(self, selection=None, parent=None):
        super().__init__(parent)
        
        # Store the current selection for fallback placement coordinates
        self.selection = selection
        
        self.setWindowTitle("ComfyUI Workflows")
        # Increased window size to give the right panel significantly more room
        self.resize(1300, 750)
        self.setMinimumWidth(1000) # Prevents the window from being squished
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        
        # Flame-like styling using Qt Stylesheets
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
        """)
        
        self.current_file_path = None
        self.data = {}
        self.entries = {}
        
        self.init_ui()
        self.scan_directory()

    def init_ui(self):
        main_layout = QtWidgets.QHBoxLayout(self)
        
        # --- Sidebar ---
        sidebar_layout = QtWidgets.QVBoxLayout()
        
        # Main Presets List
        title_lbl = QtWidgets.QLabel("Available Workflow Presets")
        title_lbl.setStyleSheet("font-weight: bold; color: #999; font-size: 14px;")
        sidebar_layout.addWidget(title_lbl)
        
        self.file_list = QtWidgets.QListWidget()
        self.file_list.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.file_list.itemSelectionChanged.connect(self.on_main_file_select)
        sidebar_layout.addWidget(self.file_list)
        
        # Sent Jobs List
        sent_title_lbl = QtWidgets.QLabel("Sent Jobs")
        sent_title_lbl.setStyleSheet("font-weight: bold; color: #999; font-size: 14px; margin-top: 10px;")
        sidebar_layout.addWidget(sent_title_lbl)
        
        self.sent_list = QtWidgets.QListWidget()
        self.sent_list.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.sent_list.itemSelectionChanged.connect(self.on_sent_file_select)
        sidebar_layout.addWidget(self.sent_list)
        
        main_layout.addLayout(sidebar_layout) 
        
        # --- Editor Area ---
        editor_layout = QtWidgets.QVBoxLayout()
        
        self.workflow_title_lbl = QtWidgets.QLabel("Select a workflow...")
        self.workflow_title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff; margin-bottom: 5px;")
        editor_layout.addWidget(self.workflow_title_lbl)
        
        self.save_btn = QtWidgets.QPushButton("Send to ComfyUI")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_file)
        editor_layout.addWidget(self.save_btn, alignment=QtCore.Qt.AlignLeft)
        
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_widget = QtWidgets.QWidget()
        self.scroll_widget.setObjectName("scrollWidget")
        
        self.form_layout = QtWidgets.QFormLayout(self.scroll_widget)
        # Ensure the form layout fields stretch out as much as possible
        self.form_layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        self.scroll_area.setWidget(self.scroll_widget)
        
        editor_layout.addWidget(self.scroll_area)
        
        main_layout.addLayout(editor_layout, 1) 

    def scan_directory(self):
        if not os.path.exists(DIRECTORY_PATH):
            try:
                os.makedirs(DIRECTORY_PATH)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Warning", f"Could not create directory:\n{e}")
                return
                
        self.file_list.clear()
        self.sent_list.clear()
        
        # 1. Populate Main List
        for f in sorted(os.listdir(DIRECTORY_PATH)):
            if f.endswith('.json') and os.path.isfile(os.path.join(DIRECTORY_PATH, f)):
                display_name = f.replace('-API_Profile.json', '')
                item = QtWidgets.QListWidgetItem(display_name)
                item.setData(QtCore.Qt.UserRole, f)
                self.file_list.addItem(item)
                
        # 2. Populate Sent List (Reverse sorted to show newest timestamps at the top)
        sent_dir = os.path.join(DIRECTORY_PATH, "sent")
        if os.path.exists(sent_dir):
            for f in sorted(os.listdir(sent_dir), reverse=True):
                if f.endswith('.json'):
                    display_name = f.replace('.json', '')
                    item = QtWidgets.QListWidgetItem(display_name)
                    item.setData(QtCore.Qt.UserRole, f)
                    self.sent_list.addItem(item)

        # --- Dynamic Sidebar Sizing ---
        max_width = 120 
        font_metrics = self.file_list.fontMetrics()
        
        def get_text_width(text):
            if hasattr(font_metrics, 'horizontalAdvance'):
                return font_metrics.horizontalAdvance(text)
            return font_metrics.width(text)
        
        # Measure widths from both lists
        for i in range(self.file_list.count()):
            max_width = max(max_width, get_text_width(self.file_list.item(i).text()))
        for i in range(self.sent_list.count()):
            max_width = max(max_width, get_text_width(self.sent_list.item(i).text()))
                
        final_width = max_width + 40
        self.file_list.setFixedWidth(final_width)
        self.sent_list.setFixedWidth(final_width)

    def on_main_file_select(self):
        selected_items = self.file_list.selectedItems()
        if not selected_items: 
            return
            
        # Quietly clear the selection in the sent list without triggering its event
        self.sent_list.blockSignals(True)
        self.sent_list.clearSelection()
        self.sent_list.blockSignals(False)
            
        item = selected_items[0]
        real_filename = item.data(QtCore.Qt.UserRole)
        self.current_file_path = os.path.join(DIRECTORY_PATH, real_filename)
        
        self.load_selected_file(real_filename)

    def on_sent_file_select(self):
        selected_items = self.sent_list.selectedItems()
        if not selected_items:
            return
            
        # Quietly clear the selection in the main list without triggering its event
        self.file_list.blockSignals(True)
        self.file_list.clearSelection()
        self.file_list.blockSignals(False)
        
        item = selected_items[0]
        real_filename = item.data(QtCore.Qt.UserRole)
        self.current_file_path = os.path.join(DIRECTORY_PATH, "sent", real_filename)
        
        self.load_selected_file(real_filename)

    def load_selected_file(self, filename_for_error):
        """ Shared method to load the JSON regardless of which list it came from. """
        try:
            with open(self.current_file_path, 'r') as f:
                self.data = json.load(f)
            self.save_btn.setEnabled(True)
            self.refresh_ui()
        except json.JSONDecodeError:
            QtWidgets.QMessageBox.critical(self, "JSON Error", f"The file {filename_for_error} contains invalid JSON.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to open config:\n{e}")

    def refresh_ui(self):
        workflow_name = self.data.get("CO_WorkflowName", "Unnamed Workflow")
        self.workflow_title_lbl.setText(f"Workflow: {workflow_name}")

        while self.form_layout.count():
            item = self.form_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.entries = {}
        for key, value in self.data.items():
            if key.endswith('ImageSrc'):
                continue
            if key == "CO_WorkflowName":
                continue
            
            if isinstance(value, (dict, list)):
                lbl = QtWidgets.QLabel("Nested Data (Uneditable)")
                lbl.setStyleSheet("color: #777; font-style: italic;")
                self.form_layout.addRow(f"{key}:", lbl)
                continue
            
            val_str = str(value)
            
            if key in ["CO_PosPrompt", "CO_Neg_Prompt", "CO_NegPrompt"]:
                text_widget = QtWidgets.QPlainTextEdit(val_str)
                text_widget.setMinimumHeight(80) 
                text_widget.setMaximumHeight(200) 
            else:
                text_widget = QtWidgets.QLineEdit(val_str)
            
            self.form_layout.addRow(f"{key}:", text_widget)
            self.entries[key] = text_widget

    def save_file(self):
        for key, entry in self.entries.items():
            if isinstance(entry, QtWidgets.QPlainTextEdit):
                val = entry.toPlainText().strip()
            else:
                val = entry.text().strip()
            
            if val.lower() == "true": 
                self.data[key] = True
            elif val.lower() == "false": 
                self.data[key] = False
            else:
                is_negative = val.startswith('-')
                check_str = val[1:] if is_negative else val
                
                if check_str.replace('.', '', 1).isdigit():
                    self.data[key] = float(val) if '.' in val else int(val)
                else:
                    self.data[key] = val

        try:
            timestamp = datetime.datetime.now().strftime("%H%M%S")
            base_name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            
            # Clean up existing timestamp suffix if the user resends an already-sent job
            base_name = re.sub(r'_\d{6}$', '', base_name)
            
            # Formulate the new filename and node name
            new_name = f"{base_name}_{timestamp}"
            
            # Ensure the sent directory exists
            sent_dir = os.path.join(DIRECTORY_PATH, "sent")
            if not os.path.exists(sent_dir):
                os.makedirs(sent_dir)
                
            new_file_path = os.path.join(sent_dir, f"{new_name}.json")
            json_string = json.dumps(self.data, indent=4)
            
            with open(new_file_path, 'w') as f:
                f.write(json_string)
            
            # Create Flame Note Node
            if flame.batch:
                note_node = flame.batch.create_node("Note")
                note_node.name = new_name
                note_node.note_collapsed = True
                
                try:
                    cursor_pos = flame.batch.cursor_position
                    note_node.pos_x = cursor_pos[0]
                    note_node.pos_y = cursor_pos[1]
                except AttributeError:
                    if self.selection:
                        note_node.pos_x = self.selection[0].pos_x + 150
                        note_node.pos_y = self.selection[0].pos_y
                
                try:
                    note_node.note = json_string
                except AttributeError:
                    note_node.note.value = json_string

            # Trigger a sidebar refresh to show the newly created file
            self.scan_directory()

            QtWidgets.QMessageBox.information(self, "Success", f"Sent to ComfyUI successfully!\n\nFile saved to sent folder: {new_name}.json\nNode created: {new_name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save Error", f"Failed to send to ComfyUI:\n{str(e)}")

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        key = event.key()

        focused_widget = QtWidgets.QApplication.focusWidget()
        is_text_input = isinstance(focused_widget, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit))

        if is_text_input:
            if modifiers == QtCore.Qt.ControlModifier and key == QtCore.Qt.Key_Z:
                focused_widget.undo()
                event.accept()
                return
            elif (modifiers == (QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier) and key == QtCore.Qt.Key_Z) or \
                 (modifiers == QtCore.Qt.ControlModifier and key == QtCore.Qt.Key_Y):
                focused_widget.redo()
                event.accept()
                return

        super().keyPressEvent(event)

# --- FLAME BATCH HOOK ---

_editor_window = None 

def launch_editor(selection):
    global _editor_window
    _editor_window = FlameBatchJsonEditor(selection)
    _editor_window.show()

def get_batch_custom_ui_actions():
    return [
        {
            "name": "Testing_CO_ComfyUI",
            "actions": [
                {
                    "name": "Send ComfyUI Job",
                    "execute": lambda selection: launch_editor(selection)
                }
            ]
        }
    ]