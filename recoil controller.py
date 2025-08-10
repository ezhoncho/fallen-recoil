#!/usr/bin/env python3
from __future__ import annotations
import sys
import os
import json
import time
import logging
from dataclasses import dataclass, asdict
from threading import Event, Lock
from typing import Dict, Any, Optional

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QDoubleSpinBox,
    QPushButton, QVBoxLayout, QHBoxLayout, QListWidget, QFrame,
    QMessageBox, QLineEdit, QFileDialog, QSlider, QGroupBox
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QObject, QTimer
from PyQt5.QtGui import QPalette, QColor, QFont

from pynput import mouse, keyboard
import win32api, win32con

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.expanduser("~"), "recoil_controller.log")),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("RecoilController")

PRESET_PATH = os.path.join(os.path.expanduser("~"), ".macro_presets_xyz_roblox.json")

# Windows raw delta mouse move
def move_mouse_raw(dx: int, dy: int):
    try:
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, dx, dy, 0, 0)
    except Exception as e:
        logger.error(f"Mouse movement failed: {str(e)}")

def scroll_mouse(amount: int):
    try:
        win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, amount * 120, 0)
    except Exception as e:
        logger.error(f"Mouse scroll failed: {str(e)}")


@dataclass
class Preset:
    name: str
    move_x: float
    move_y: float
    move_z: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Preset":
        return Preset(
            name=d.get("name", "preset"),
            move_x=float(d.get("move_x", 0.0)),
            move_y=float(d.get("move_y", 0.0)),
            move_z=float(d.get("move_z", 0.0)),
        )


class CompensatorWorker(QThread):
    status = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, active_event: Event, crouch_event: Event, crouch_factor_getter):
        super().__init__()
        self.active_event = active_event
        self.crouch_event = crouch_event
        self._stop_event = Event()
        self.preset: Optional[Preset] = None
        self._accum_x = 0.0
        self._accum_y = 0.0
        self._accum_z = 0.0
        self.get_crouch_factor = crouch_factor_getter
        self.preset_lock = Lock()
        self.left_button_held = False  # Track if left mouse button is held down
        self.compensation_interval = 0.01  # Configurable interval for auto-compensation (10ms)

    def set_preset(self, preset: Preset):
        with self.preset_lock:
            self.preset = preset

    def stop(self):
        self._stop_event.set()
        self.active_event.clear()
        self.left_button_held = False  # Reset button state on stop
        if not self.wait(2000):  # Wait up to 2 seconds for thread to finish
            logger.warning("Worker thread did not terminate gracefully")
    def set_left_button_state(self, held: bool):
        """Set the left mouse button state for continuous compensation"""
        self.left_button_held = held
        
    def set_compensation_interval(self, interval: float):
        """Set the interval between compensation applications for auto-fire"""
        self.compensation_interval = max(0.001, min(0.1, interval))  # Clamp between 1ms and 100ms

    def run(self):
        logger.info("Compensator worker started")
        try:
            while not self._stop_event.is_set():
                try:
                    if self.active_event.is_set() and self.preset and self.left_button_held:
                        # Apply continuous recoil compensation while left button is held
                        factor = self.get_crouch_factor() if self.crouch_event.is_set() else 1.0
                        
                        # Use lock when accessing preset
                        with self.preset_lock:
                            # Apply compensation continuously at the set interval
                            self._accum_x += self.preset.move_x * factor
                            self._accum_y += self.preset.move_y * factor
                            self._accum_z += self.preset.move_z * factor

                        # Extract integer parts to move/scroll
                        move_x_int = int(self._accum_x)
                        move_y_int = int(self._accum_y)
                        move_z_int = int(self._accum_z)

                        # Subtract sent integers from accumulator
                        self._accum_x -= move_x_int
                        self._accum_y -= move_y_int
                        self._accum_z -= move_z_int

                        # Send raw movement deltas so Roblox detects it in mouse lock
                        if move_x_int != 0 or move_y_int != 0:
                            move_mouse_raw(move_x_int, move_y_int)

                        # Scroll if Z != 0
                        if move_z_int != 0:
                            scroll_mouse(move_z_int)
                        
                        # Use compensation interval for continuous auto-compensation
                        time.sleep(self.compensation_interval)
                    else:
                        # When not compensating, use a longer sleep to reduce CPU usage
                        time.sleep(0.01)
                        
                except Exception as e:
                    logger.error(f"Error in worker loop: {str(e)}")
                    self.error.emit(f"Worker error: {str(e)}")
                    time.sleep(1)  # Prevent tight error loop
        except Exception as e:
            logger.critical(f"Critical error in worker thread: {str(e)}")
            self.error.emit(f"Critical worker error: {str(e)}")
        finally:
            logger.info("Compensator worker stopped")


class SafeMouseListener(QObject):
    button_pressed = pyqtSignal(mouse.Button, bool)
    
    def __init__(self):
        super().__init__()
        self.listener = None
        self.running = False
        
    def start(self):
        if self.running:
            return
            
        self.running = True
        self.listener = mouse.Listener(on_click=self.on_click)
        self.listener.daemon = True
        self.listener.start()
        logger.info("Mouse listener started")
        
    def stop(self):
        if not self.running:
            return
            
        self.running = False
        if self.listener:
            try:
                self.listener.stop()
            except Exception as e:
                logger.error(f"Error stopping mouse listener: {str(e)}")
        logger.info("Mouse listener stopped")
        
    def on_click(self, x: int, y: int, button: mouse.Button, pressed: bool):
        try:
            self.button_pressed.emit(button, pressed)
        except Exception as e:
            logger.error(f"Error in mouse handler: {str(e)}")


class SafeKeyboardListener(QObject):
    key_pressed = pyqtSignal(object, bool)  # Changed to object to handle both Key and KeyCode types
    
    def __init__(self):
        super().__init__()
        self.listener = None
        self.running = False
        
    def start(self):
        if self.running:
            return
            
        self.running = True
        self.listener = keyboard.Listener(
            on_press=lambda k: self.on_key(k, True),
            on_release=lambda k: self.on_key(k, False)
        )
        self.listener.daemon = True
        self.listener.start()
        logger.info("Keyboard listener started")
        
    def stop(self):
        if not self.running:
            return
            
        self.running = False
        if self.listener:
            try:
                self.listener.stop()
            except Exception as e:
                logger.error(f"Error stopping keyboard listener: {str(e)}")
        logger.info("Keyboard listener stopped")
        
    def on_key(self, key, pressed):
        try:
            self.key_pressed.emit(key, pressed)
        except Exception as e:
            logger.error(f"Error in keyboard handler: {str(e)}")


class MacroController(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Roblox Recoil Compensator")
        self.resize(500, 450)
        
        # Apply dark theme
        self.setStyleSheet("""
            QWidget {
                background-color: #2D2D30;
                color: #DCDCDC;
                font-family: 'Segoe UI';
            }
            QGroupBox {
                border: 1px solid #3C3C40;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 15px;
                font-weight: bold;
                color: #DCDCDC;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                background-color: transparent;
            }
            QLabel {
                color: #DCDCDC;
            }
            QPushButton {
                background-color: #3C3C40;
                color: #DCDCDC;
                border: 1px solid #3C3C40;
                border-radius: 4px;
                padding: 6px 12px;
                min-height: 24px;
            }
            QPushButton:hover {
                background-color: #007ACC;
                border: 1px solid #007ACC;
            }
            QPushButton:pressed {
                background-color: #005A9E;
            }
            QPushButton:disabled {
                background-color: #252526;
                color: #6D6D6D;
            }
            QLineEdit, QListWidget, QDoubleSpinBox {
                background-color: #252526;
                color: #DCDCDC;
                border: 1px solid #3C3C40;
                border-radius: 4px;
                padding: 4px;
            }
            QSlider::groove:horizontal {
                background: #252526;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #007ACC;
                border: 1px solid #007ACC;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::sub-page:horizontal {
                background: #007ACC;
                border-radius: 3px;
            }
            QListWidget::item {
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #007ACC;
                color: white;
            }
            #header {
                background-color: #007ACC;
                color: white;
                font-weight: bold;
                font-size: 18px;
                padding: 12px;
                border-radius: 4px;
            }
            #statusActive {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            #statusInactive {
                background-color: #F44336;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            #statusError {
                background-color: #FF9800;
                color: white;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
            }
            #presetControls {
                background-color: #252526;
                border: 1px solid #3C3C40;
                border-radius: 4px;
                padding: 10px;
            }
        """)

        # Create header
        header = QLabel("ROBLOX RECOIL COMPENSATOR")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignCenter)
        header.setFont(QFont("Segoe UI", 14, QFont.Bold))
        
        # Create status bar
        self.status_label = QLabel("Status: Initializing...")
        self.status_label.setObjectName("statusInactive")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        
        # Setup main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.addWidget(header)
        
        # Recoil settings group
        recoil_group = QGroupBox("Recoil Compensation Settings")
        recoil_layout = QVBoxLayout()
        recoil_layout.setSpacing(10)
        
        # X movement
        x_layout = QHBoxLayout()
        self.move_x_label = QLabel("Horizontal Compensation (X):")
        self.move_x_label.setFixedWidth(200)
        self.move_x_spin = QDoubleSpinBox()
        self.move_x_spin.setRange(-10.0, 10.0)
        self.move_x_spin.setDecimals(2)
        self.move_x_spin.setSingleStep(0.1)
        self.move_x_spin.setValue(0.0)
        self.move_x_spin.setFixedWidth(120)
        x_layout.addWidget(self.move_x_label)
        x_layout.addWidget(self.move_x_spin)
        recoil_layout.addLayout(x_layout)
        
        # Y movement
        y_layout = QHBoxLayout()
        self.move_y_label = QLabel("Vertical Compensation (Y):")
        self.move_y_label.setFixedWidth(200)
        self.move_y_spin = QDoubleSpinBox()
        self.move_y_spin.setRange(-10.0, 10.0)
        self.move_y_spin.setDecimals(2)
        self.move_y_spin.setSingleStep(0.1)
        self.move_y_spin.setValue(-0.7)  # Default downward compensation
        self.move_y_spin.setFixedWidth(120)
        y_layout.addWidget(self.move_y_label)
        y_layout.addWidget(self.move_y_spin)
        recoil_layout.addLayout(y_layout)
        
        # Z movement
        z_layout = QHBoxLayout()
        self.move_z_label = QLabel("Scroll Compensation (Z):")
        self.move_z_label.setFixedWidth(200)
        self.move_z_spin = QDoubleSpinBox()
        self.move_z_spin.setRange(-10.0, 10.0)
        self.move_z_spin.setDecimals(2)
        self.move_z_spin.setSingleStep(0.1)
        self.move_z_spin.setValue(0.0)
        self.move_z_spin.setFixedWidth(120)
        z_layout.addWidget(self.move_z_label)
        z_layout.addWidget(self.move_z_spin)
        recoil_layout.addLayout(z_layout)
        
        # Compensation interval
        interval_layout = QHBoxLayout()
        self.interval_label = QLabel("Auto-fire Interval (ms):")
        self.interval_label.setFixedWidth(200)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(1.0, 100.0)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setSingleStep(1.0)
        self.interval_spin.setValue(10.0)  # Default 10ms for typical auto weapons
        self.interval_spin.setFixedWidth(120)
        interval_layout.addWidget(self.interval_label)
        interval_layout.addWidget(self.interval_spin)
        recoil_layout.addLayout(interval_layout)
        
        recoil_group.setLayout(recoil_layout)
        
        # Crouch settings group
        crouch_group = QGroupBox("Crouch Settings")
        crouch_layout = QVBoxLayout()
        crouch_layout.setSpacing(10)
        
        # Crouch key
        key_layout = QHBoxLayout()
        self.crouch_key_label = QLabel("Crouch Key:")
        self.crouch_key_label.setFixedWidth(200)
        self.crouch_key_edit = QLineEdit()
        self.crouch_key_edit.setPlaceholderText("ctrl, shift, alt, c")
        self.crouch_key_edit.setText("ctrl")  # default
        key_layout.addWidget(self.crouch_key_label)
        key_layout.addWidget(self.crouch_key_edit)
        crouch_layout.addLayout(key_layout)
        
        # Crouch reduction slider
        slider_layout = QHBoxLayout()
        self.crouch_slider_label = QLabel("Crouch Reduction: 50%")
        self.crouch_slider_label.setFixedWidth(200)
        self.crouch_slider = QSlider(Qt.Horizontal)
        self.crouch_slider.setRange(0, 100)
        self.crouch_slider.setValue(50)  # default 50%
        slider_layout.addWidget(self.crouch_slider_label)
        slider_layout.addWidget(self.crouch_slider)
        crouch_layout.addLayout(slider_layout)
        
        crouch_group.setLayout(crouch_layout)
        
        # Presets group
        presets_group = QGroupBox("Presets")
        presets_layout = QVBoxLayout()
        
        # Presets list
        self.preset_list = QListWidget()
        presets_layout.addWidget(self.preset_list, 1)
        
        # Preset controls
        controls_layout = QHBoxLayout()
        
        self.save_name_edit = QLineEdit()
        self.save_name_edit.setPlaceholderText("Enter preset name")
        controls_layout.addWidget(self.save_name_edit, 1)
        
        self.save_btn = QPushButton("Save")
        self.load_btn = QPushButton("Load")
        self.delete_btn = QPushButton("Delete")
        
        controls_layout.addWidget(self.save_btn)
        controls_layout.addWidget(self.load_btn)
        controls_layout.addWidget(self.delete_btn)
        
        presets_layout.addLayout(controls_layout)
        presets_group.setLayout(presets_layout)
        
        # Enable button
        self.enable_btn = QPushButton("Enable Auto-Compensation")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(True)
        self.enable_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 12px;
                border-radius: 4px;
                margin-top: 10px;
                margin-bottom: 10px;
            }
            QPushButton:checked {
                background-color: #F44336;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:checked:hover {
                background-color: #d32f2f;
            }
        """)
        
        # Message for auto-compensation behavior
        self.shot_info = QLabel("Hold left mouse button - continuous auto-compensation while held")
        self.shot_info.setAlignment(Qt.AlignCenter)
        self.shot_info.setStyleSheet("font-style: italic; color: #AAAAAA;")
        
        # Add everything to main layout
        main_layout.addWidget(recoil_group)
        main_layout.addWidget(crouch_group)
        main_layout.addWidget(presets_group)
        main_layout.addWidget(self.enable_btn)
        main_layout.addWidget(self.shot_info)
        main_layout.addWidget(self.status_label)
        
        self.setLayout(main_layout)

        # Worker and events
        self.active_event = Event()
        self.crouch_event = Event()
        self.worker = CompensatorWorker(self.active_event, self.crouch_event, self.get_crouch_factor)
        self.worker.error.connect(self.handle_worker_error)
        
        # Safe listeners
        self.mouse_listener = SafeMouseListener()
        self.keyboard_listener = SafeKeyboardListener()
        
        # State
        self.left_pressed = False
        self.right_pressed = False
        self.pressed_keys = set()
        self.crouch_key = "ctrl"  # Default
        self.presets: Dict[str, Preset] = {}
        
        # Load presets after UI is ready
        QTimer.singleShot(100, self.initialize_application)

    def initialize_application(self):
        """Initialize application components safely after UI is shown"""
        try:
            self.load_presets()
            self.refresh_preset_list()
            self.worker.start()
            self.mouse_listener.start()
            self.keyboard_listener.start()
            self.connect_signals()
            
            # Set initial compensation interval
            initial_interval = self.interval_spin.value() / 1000.0  # Convert ms to seconds
            self.worker.set_compensation_interval(initial_interval)
            
            self.status_label.setText("Status: Ready")
            # Set default crouch key
            self.crouch_key = self.crouch_key_edit.text().strip().lower()
            logger.info("Application initialized")
        except Exception as e:
            logger.critical(f"Initialization failed: {str(e)}")
            self.show_error(f"Initialization failed: {str(e)}")
            self.status_label.setObjectName("statusError")
            self.status_label.setText("Initialization failed!")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def get_crouch_factor(self) -> float:
        # Return factor from slider percentage (0.0 to 1.0)
        try:
            return self.crouch_slider.value() / 100.0
        except Exception as e:
            logger.error(f"Error getting crouch factor: {str(e)}")
            return 1.0

    def on_crouch_slider_changed(self, value: int):
        try:
            self.crouch_slider_label.setText(f"Crouch Reduction: {value}%")
        except Exception as e:
            logger.error(f"Slider change error: {str(e)}")

    # ---------- Preset management ----------
    def load_presets(self):
        try:
            if os.path.exists(PRESET_PATH):
                with open(PRESET_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for name, pd in data.items():
                    self.presets[name] = Preset.from_dict(pd)
                logger.info(f"Loaded {len(self.presets)} presets")
        except Exception as e:
            logger.error(f"Failed to load presets: {str(e)}")

    def save_presets_file(self):
        try:
            data = {name: p.to_dict() for name, p in self.presets.items()}
            with open(PRESET_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info("Presets saved")
        except Exception as e:
            logger.error(f"Failed saving presets: {str(e)}")
            self.show_error(f"Failed to save presets: {str(e)}")

    def refresh_preset_list(self):
        try:
            self.preset_list.clear()
            for name in sorted(self.presets.keys()):
                self.preset_list.addItem(name)
        except Exception as e:
            logger.error(f"Error refreshing preset list: {str(e)}")

    def save_current_preset(self):
        try:
            name = self.save_name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "Preset name required", "Please enter a preset name.")
                return
                
            p = Preset(
                name=name,
                move_x=self.move_x_spin.value(),
                move_y=self.move_y_spin.value(),
                move_z=self.move_z_spin.value(),
            )
            
            self.presets[name] = p
            self.save_presets_file()
            self.refresh_preset_list()
            self.save_name_edit.clear()
            self.status_label.setText(f"Saved preset '{name}'")
            logger.info(f"Saved preset: {name}")
        except Exception as e:
            logger.error(f"Error saving preset: {str(e)}")
            self.show_error(f"Failed to save preset: {str(e)}")

    def load_selected_preset(self):
        try:
            item = self.preset_list.currentItem()
            if not item:
                QMessageBox.information(self, "Select preset", "Please select a preset from the list.")
                return
                
            name = item.text()
            p = self.presets.get(name)
            if not p:
                return
                
            self.move_x_spin.setValue(p.move_x)
            self.move_y_spin.setValue(p.move_y)
            self.move_z_spin.setValue(p.move_z)
                
            self.status_label.setText(f"Loaded preset '{name}'")
            logger.info(f"Loaded preset: {name}")
        except Exception as e:
            logger.error(f"Error loading preset: {str(e)}")
            self.show_error(f"Failed to load preset: {str(e)}")

    def delete_selected_preset(self):
        try:
            item = self.preset_list.currentItem()
            if not item:
                return
            name = item.text()
            if name in self.presets:
                del self.presets[name]
                self.save_presets_file()
                self.refresh_preset_list()
                self.status_label.setText(f"Deleted preset '{name}'")
                logger.info(f"Deleted preset: {name}")
        except Exception as e:
            logger.error(f"Error deleting preset: {str(e)}")
            self.show_error(f"Failed to delete preset: {str(e)}")

    # ---------- Mouse Listener ----------
    def on_mouse_click(self, button: mouse.Button, pressed: bool):
        try:
            if button == mouse.Button.left:
                self.left_pressed = pressed
                if self.enable_btn.isChecked():
                    if pressed:
                        # Start continuous compensation when left button is pressed
                        self.start_continuous_compensation()
                    else:
                        # Stop continuous compensation when left button is released
                        self.stop_continuous_compensation()
                    
            elif button == mouse.Button.right:
                self.right_pressed = pressed
                
            # Update status label
            if self.enable_btn.isChecked():
                if button == mouse.Button.left:
                    if pressed:
                        self.status_label.setObjectName("statusActive")
                        self.status_label.setText("Auto-compensation active")
                        self.status_label.style().unpolish(self.status_label)
                        self.status_label.style().polish(self.status_label)
                    else:
                        self.status_label.setObjectName("statusInactive")
                        self.status_label.setText("Ready (hold left button for auto-compensation)")
                        self.status_label.style().unpolish(self.status_label)
                        self.status_label.style().polish(self.status_label)
                    
        except Exception as e:
            logger.error(f"Mouse click error: {str(e)}")
            
    def start_continuous_compensation(self):
        """Start continuous recoil compensation while left button is held"""
        try:
            if not self.enable_btn.isChecked():
                return
                
            # Create preset from current settings
            p = Preset(
                name="live",
                move_x=self.move_x_spin.value(),
                move_y=self.move_y_spin.value(),
                move_z=self.move_z_spin.value(),
            )
            
            # Set active and start continuous compensation
            self.worker.set_preset(p)
            self.worker.set_left_button_state(True)
            self.active_event.set()
            
        except Exception as e:
            logger.error(f"Start compensation error: {str(e)}")
            
    def stop_continuous_compensation(self):
        """Stop continuous recoil compensation"""
        try:
            self.worker.set_left_button_state(False)
            self.active_event.clear()
            
        except Exception as e:
            logger.error(f"Stop compensation error: {str(e)}")

    def apply_compensation(self):
        """Legacy method - now redirects to continuous compensation for compatibility"""
        self.start_continuous_compensation()

    # ---------- Keyboard Listener ----------
    def on_key_action(self, key, pressed):
        try:
            k = self.key_to_str(key)
            if pressed:
                self.pressed_keys.add(k)
                if self.crouch_key and k == self.crouch_key:
                    self.crouch_event.set()
            else:
                if k in self.pressed_keys:
                    self.pressed_keys.remove(k)
                if self.crouch_key and k == self.crouch_key:
                    self.crouch_event.clear()
        except Exception as e:
            logger.error(f"Key action error: {str(e)}")

    def key_to_str(self, key):
        try:
            # Handle both Key and KeyCode objects from pynput
            if hasattr(key, "char") and key.char is not None:
                return key.char.lower()
            elif hasattr(key, "name"):
                return key.name.lower()
            elif hasattr(key, "_name_"):
                return key._name_.lower()
            else:
                # For other key types, convert to string and extract name
                key_str = str(key).lower()
                if "key." in key_str:
                    return key_str.split("key.")[-1]
                if "keycode." in key_str:  
                    return key_str.split("keycode.")[-1]
                return key_str
        except Exception as e:
            logger.error(f"Key conversion error: {str(e)}")
            return "unknown"

    def on_crouch_key_changed(self, text: str):
        try:
            key = text.strip().lower()
            if not key:
                self.crouch_key = None
                self.status_label.setText("Crouch key cleared")
                self.crouch_event.clear()
            else:
                self.crouch_key = key
                self.status_label.setText(f"Crouch key set to '{key}'")
        except Exception as e:
            logger.error(f"Crouch key change error: {str(e)}")

    def on_toggle_enabled(self, enabled: bool):
        try:
            if enabled:
                self.status_label.setText("Auto-Compensation Enabled")
                self.enable_btn.setText("Disable Auto-Compensation")
            else:
                self.status_label.setText("Auto-Compensation Disabled")
                self.enable_btn.setText("Enable Auto-Compensation")
                self.active_event.clear()
                self.worker.set_left_button_state(False)
        except Exception as e:
            logger.error(f"Toggle error: {str(e)}")

    def on_interval_changed(self, value: float):
        """Update the compensation interval when changed"""
        try:
            interval_seconds = value / 1000.0  # Convert ms to seconds
            self.worker.set_compensation_interval(interval_seconds)
            logger.info(f"Compensation interval set to {value}ms")
        except Exception as e:
            logger.error(f"Interval change error: {str(e)}")

    def connect_signals(self):
        try:
            # Connect UI signals
            self.save_btn.clicked.connect(self.save_current_preset)
            self.load_btn.clicked.connect(self.load_selected_preset)
            self.delete_btn.clicked.connect(self.delete_selected_preset)
            self.enable_btn.toggled.connect(self.on_toggle_enabled)
            self.preset_list.itemDoubleClicked.connect(self.load_selected_preset)
            self.crouch_key_edit.textChanged.connect(self.on_crouch_key_changed)
            self.crouch_slider.valueChanged.connect(self.on_crouch_slider_changed)
            self.interval_spin.valueChanged.connect(self.on_interval_changed)
            
            # Connect input listeners
            self.mouse_listener.button_pressed.connect(self.on_mouse_click)
            self.keyboard_listener.key_pressed.connect(self.on_key_action)
            logger.info("Signals connected")
        except Exception as e:
            logger.error(f"Signal connection error: {str(e)}")

    def handle_worker_error(self, message):
        try:
            self.status_label.setObjectName("statusError")
            self.status_label.setText(message)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            logger.error(f"Worker error: {message}")
        except Exception as e:
            logger.error(f"Error handling worker error: {str(e)}")

    def show_error(self, message):
        QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        try:
            logger.info("Shutting down application")
            # Stop continuous compensation first
            self.worker.set_left_button_state(False)
            self.active_event.clear()
            # Then stop all threads
            self.worker.stop()
            self.mouse_listener.stop()
            self.keyboard_listener.stop()
            self.save_presets_file()
            logger.info("Application shutdown complete")
        except Exception as e:
            logger.error(f"Shutdown error: {str(e)}")
        finally:
            event.accept()


def main():
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle("Fusion")
    
    # Create and customize palette
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(45, 45, 48))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(37, 37, 38))
    palette.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
    palette.setColor(QPalette.ToolTipBase, QColor(0, 122, 204))
    palette.setColor(QPalette.ToolTipText, Qt.white)
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(60, 60, 64))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Highlight, QColor(0, 122, 204))
    palette.setColor(QPalette.HighlightedText, Qt.white)
    
    app.setPalette(palette)
    
    w = MacroController()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()