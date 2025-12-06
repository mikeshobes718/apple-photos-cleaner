#!/usr/bin/env python3
"""
Photo Cleaner Pro - Premium Desktop App
"""

import os
import sys
import json
import base64
import subprocess
import time
from io import BytesIO
from datetime import datetime

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7
ALBUM_NAME = "🤖 AI Matches"

PHOTOS_LIBRARY = os.path.expanduser("~/Pictures/Photos Library.photoslibrary")
PHOTO_PATHS = [
    os.path.join(PHOTOS_LIBRARY, "originals"),
    os.path.join(PHOTOS_LIBRARY, "resources/renders"),
    os.path.join(PHOTOS_LIBRARY, "resources/derivatives"),
]
VALID_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.webp', '.gif', '.tiff'}

def load_api_key():
    if not os.path.exists(ENV_PATH):
        return None
    try:
        with open(ENV_PATH) as f:
            return json.load(f).get("OPENAI_API_KEY")
    except:
        return None

def count_photos():
    """Quick count of available photos."""
    count = 0
    seen = set()
    for base in PHOTO_PATHS:
        if not os.path.exists(base):
            continue
        for root, _, files in os.walk(base):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in VALID_EXTENSIONS:
                    continue
                uuid = f.split('_')[0] if '_' in f else os.path.splitext(f)[0]
                if uuid not in seen:
                    seen.add(uuid)
                    count += 1
    return count

def get_local_photos(limit=None):
    photos = []
    seen = set()
    for base in PHOTO_PATHS:
        if not os.path.exists(base):
            continue
        for root, _, files in os.walk(base):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in VALID_EXTENSIONS:
                    continue
                uuid = f.split('_')[0] if '_' in f else os.path.splitext(f)[0]
                if uuid in seen:
                    continue
                seen.add(uuid)
                photos.append({'filename': f, 'path': os.path.join(root, f), 'uuid': uuid})
                if limit and len(photos) >= limit:
                    return photos
    return photos

def add_to_album(filename, uuid):
    """Add a photo to the AI Matches album by UUID or filename. Skips if already in album."""
    script = f'''
    tell application "Photos"
        try
            set targetAlbum to album "{ALBUM_NAME}"
        on error
            set targetAlbum to make new album named "{ALBUM_NAME}"
        end try
        
        -- Get existing photos in album to avoid duplicates
        set existingIds to id of every media item in targetAlbum
        
        -- Try by ID first (UUID)
        try
            set matchedPhotos to (every media item whose id contains "{uuid}")
            if (count of matchedPhotos) > 0 then
                set thePhoto to item 1 of matchedPhotos
                if (id of thePhoto) is not in existingIds then
                    add {{thePhoto}} to targetAlbum
                end if
                return
            end if
        end try
        
        -- Try by filename containing UUID
        try
            set matchedPhotos to (every media item whose filename contains "{uuid}")
            if (count of matchedPhotos) > 0 then
                set thePhoto to item 1 of matchedPhotos
                if (id of thePhoto) is not in existingIds then
                    add {{thePhoto}} to targetAlbum
                end if
            end if
        end try
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
    except:
        pass

# ============================================================
# PyQt6
# ============================================================
try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QTimer
    from PyQt6.QtGui import QPixmap, QImage, QAction, QFont
except ImportError:
    sys.exit(1)

try:
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except:
        pass
except ImportError:
    sys.exit(1)

try:
    import openai
except ImportError:
    sys.exit(1)


# ============================================================
# Themes
# ============================================================
DARK = {
    "bg": "#121212",
    "surface": "#1e1e1e",
    "surface2": "#2a2a2a",
    "surface3": "#363636",
    "text": "#f5f5f5",
    "text2": "#a0a0a0",
    "text3": "#6a6a6a",
    "accent": "#a78bfa",
    "accent2": "#8b5cf6",
    "green": "#4ade80",
    "green_bg": "rgba(74, 222, 128, 0.15)",
    "orange": "#fbbf24",
    "red": "#f87171",
}

LIGHT = {
    "bg": "#f5f5f7",
    "surface": "#ffffff",
    "surface2": "#f0f0f2",
    "surface3": "#e5e5e7",
    "text": "#1d1d1f",
    "text2": "#6e6e73",
    "text3": "#aeaeb2",
    "accent": "#7c3aed",
    "accent2": "#6d28d9",
    "green": "#22c55e",
    "green_bg": "rgba(34, 197, 94, 0.12)",
    "orange": "#f59e0b",
    "red": "#ef4444",
}


# ============================================================
# Scanner
# ============================================================
class ScannerThread(QThread):
    progress = pyqtSignal(int, int, float, int)  # current, total, speed, eta_seconds
    photo_scanned = pyqtSignal(dict)
    finished_scan = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, description, limit=None):
        super().__init__()
        self.description = description
        self.limit = limit
        self.running = True
        self.stats = {"scanned": 0, "matched": 0, "cost": 0.0}
        self.matches = []
        self.start_time = None
    
    def stop(self):
        self.running = False
    
    def run(self):
        api_key = load_api_key()
        if not api_key:
            self.error.emit("No API key")
            return
        
        photos = get_local_photos(self.limit)
        if not photos:
            self.error.emit("No photos")
            return
        
        client = openai.OpenAI(api_key=api_key)
        total = len(photos)
        self.start_time = time.time()
        
        for i, p in enumerate(photos):
            if not self.running:
                break
            
            # Calculate speed and ETA
            elapsed = time.time() - self.start_time
            speed = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = total - (i + 1)
            eta = int(remaining / speed) if speed > 0 else 0
            
            self.progress.emit(i + 1, total, speed, eta)
            
            try:
                with Image.open(p['path']) as img:
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))
                    buf = BytesIO()
                    img.save(buf, format='JPEG', quality=85)
                    img_data = base64.b64encode(buf.getvalue()).decode()
            except:
                continue
            
            try:
                r = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": 'Return JSON: {"match": bool, "confidence": 0-1, "reason": "brief"}'},
                        {"role": "user", "content": [
                            {"type": "text", "text": f'Match: "{self.description}"?'},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}", "detail": "low"}}
                        ]}
                    ],
                    max_tokens=80, timeout=30
                )
                content = r.choices[0].message.content.strip()
                if "```" in content:
                    content = content.split("```")[1].replace("json", "").strip()
                result = json.loads(content)
            except:
                result = {"match": False, "confidence": 0, "reason": "Error"}
            
            self.stats["scanned"] += 1
            self.stats["cost"] += 0.00015
            
            is_match = result.get("match") and result.get("confidence", 0) >= CONFIDENCE_THRESHOLD
            data = {**p, "is_match": is_match, "confidence": result.get("confidence", 0), "reason": result.get("reason", "")}
            
            if is_match:
                self.stats["matched"] += 1
                self.matches.append(data)
            
            self.photo_scanned.emit(data)
        
        self.stats["matches"] = self.matches
        self.finished_scan.emit(self.stats)


# ============================================================
# Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("PhotoCleanerPro", "App")
        self.scanner = None
        self.matches = []
        self._pixmap = None
        self.total_photos = 0
        
        # Theme
        self.dark_mode = self.settings.value("dark", self.is_system_dark(), type=bool)
        self.t = DARK if self.dark_mode else LIGHT
        
        self.setup_menu()
        self.setup_ui()
        self.apply_theme()
        
        # Initial scale
        QTimer.singleShot(50, self.scale_ui)
        
        # Clock timer
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()
        
        # Count photos on startup
        QTimer.singleShot(100, self.count_library)
    
    def setup_menu(self):
        menubar = self.menuBar()
        menubar.setNativeMenuBar(True)  # Use native macOS menu bar
        
        # Photo Cleaner Pro menu (app menu)
        app_menu = menubar.addMenu("Photo Cleaner Pro")
        
        restart = QAction("Restart App", self)
        restart.setShortcut("Ctrl+R")  # Shows as ⌘R on macOS
        restart.triggered.connect(self.restart_app)
        app_menu.addAction(restart)
        
        clear = QAction("Clear Results", self)
        clear.setShortcut("Ctrl+Shift+C")
        clear.triggered.connect(self.clear_results)
        app_menu.addAction(clear)
        
        app_menu.addSeparator()
        
        toggle_theme = QAction("Toggle Dark/Light Mode", self)
        toggle_theme.setShortcut("Ctrl+T")
        toggle_theme.triggered.connect(self.toggle_theme)
        app_menu.addAction(toggle_theme)
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        restart2 = QAction("Restart App", self)
        restart2.setShortcut("Ctrl+R")
        restart2.triggered.connect(self.restart_app)
        file_menu.addAction(restart2)
        
        clear2 = QAction("Clear Results", self)
        clear2.triggered.connect(self.clear_results)
        file_menu.addAction(clear2)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        toggle2 = QAction("Toggle Dark/Light Mode", self)
        toggle2.setShortcut("Ctrl+T")
        toggle2.triggered.connect(self.toggle_theme)
        view_menu.addAction(toggle2)
    
    def restart_app(self):
        if self.scanner and self.scanner.isRunning():
            self.scanner.stop()
            self.scanner.wait()
        QApplication.quit()
        subprocess.Popen([sys.executable] + sys.argv)
    
    def clear_results(self):
        self.matches = []
        self._pixmap = None
        self.refresh_matches()
        self.match_count.setText("0")
        self.update_stat(self.stat_scanned, "0")
        self.update_stat(self.stat_matches, "0")
        self.update_stat(self.stat_cost, "$0.00")
        self.photo_label.clear()
        self.photo_label.setText("◎")
        self.name_label.setText("")
        self.reason_label.setText("")
        self.match_badge.hide()
        self.status_label.setText("Ready")
        self.progress_label.setText("")
        self.progress_bar.setValue(0)
    
    def is_system_dark(self):
        try:
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
            return "dark" in r.stdout.lower()
        except:
            return True
    
    def count_library(self):
        self.total_photos = count_photos()
        self.library_label.setText(f"{self.total_photos:,} photos in library")
    
    def update_clock(self):
        now = datetime.now()
        self.time_label.setText(now.strftime("%-I:%M %p"))
        self.date_label.setText(now.strftime("%a, %b %d"))
    
    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.t = DARK if self.dark_mode else LIGHT
        self.settings.setValue("dark", self.dark_mode)
        self.theme_btn.setText("☀" if self.dark_mode else "☾")
        self.apply_theme()
        self.scale_ui()
    
    def apply_theme(self):
        t = self.t
        self.setStyleSheet(f"""
            QMainWindow {{ background: {t['bg']}; }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            QLineEdit {{
                background: {t['surface2']};
                border: none;
                border-radius: 10px;
                padding: 14px 16px;
                color: {t['text']};
                font-size: 14px;
            }}
            QLineEdit:focus {{ background: {t['surface3']}; }}
            QSpinBox {{
                background: {t['surface2']};
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                color: {t['text']};
            }}
            QCheckBox {{ color: {t['text2']}; }}
            QCheckBox::indicator {{
                width: 18px; height: 18px;
                border-radius: 5px;
                background: {t['surface2']};
                border: none;
            }}
            QCheckBox::indicator:checked {{
                background: {t['accent']};
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {t['surface3']}; border-radius: 2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        
        # Update components
        if hasattr(self, 'header_frame'):
            self.header_frame.setStyleSheet(f"background: {t['surface']};")
        
        if hasattr(self, 'preview_frame'):
            radius = int(16 * min(self.width() / 1200, self.height() / 800)) if self.width() > 0 else 16
            radius = max(10, min(radius, 24))
            self.preview_frame.setStyleSheet(f"""
                background: {t['surface']};
                border: none;
                border-radius: {radius}px;
            """)
        
        if hasattr(self, 'sidebar'):
            self.sidebar.setStyleSheet(f"background: {t['surface']};")
        
        if hasattr(self, 'start_btn'):
            self.start_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['accent']};
                    border: none;
                    border-radius: 10px;
                    padding: 12px 28px;
                    color: white;
                    font-weight: 600;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {t['accent2']}; }}
                QPushButton:disabled {{ background: {t['surface2']}; color: {t['text3']}; }}
            """)
        
        if hasattr(self, 'stop_btn'):
            self.stop_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(239, 68, 68, 0.15);
                    border: none;
                    border-radius: 10px;
                    padding: 12px 24px;
                    color: {t['red']};
                    font-weight: 600;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: rgba(239, 68, 68, 0.25); }}
            """)
        
        if hasattr(self, 'theme_btn'):
            self.theme_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['surface2']};
                    border: none;
                    border-radius: 8px;
                    padding: 8px 12px;
                    color: {t['text']};
                    font-size: 14px;
                }}
                QPushButton:hover {{ background: {t['surface3']}; }}
            """)
        
        self.refresh_matches()
    
    def setup_ui(self):
        self.setWindowTitle("Photo Cleaner Pro")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        
        # ═══════════════════ HEADER BAR ═══════════════════
        self.header_frame = QFrame()
        header = QHBoxLayout(self.header_frame)
        header.setContentsMargins(20, 12, 20, 12)
        
        # Logo/Title
        logo = QLabel("✨")
        logo.setStyleSheet("font-size: 20px;")
        header.addWidget(logo)
        
        title = QLabel("Photo Cleaner Pro")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        header.addWidget(title)
        
        # Divider
        header.addSpacing(20)
        
        # Library info
        self.library_label = QLabel("Counting photos...")
        self.library_label.setStyleSheet(f"color: {self.t['text2']}; font-size: 12px;")
        header.addWidget(self.library_label)
        
        header.addStretch()
        
        # API status
        api = load_api_key()
        api_dot = QLabel("●")
        api_dot.setStyleSheet(f"color: {self.t['green'] if api else self.t['red']}; font-size: 8px;")
        header.addWidget(api_dot)
        api_lbl = QLabel("API" if api else "No API")
        api_lbl.setStyleSheet(f"color: {self.t['text3']}; font-size: 11px; margin-left: 4px;")
        header.addWidget(api_lbl)
        
        header.addSpacing(20)
        
        # Theme toggle
        self.theme_btn = QPushButton("☀" if self.dark_mode else "☾")
        self.theme_btn.setFixedSize(32, 28)
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme_btn)
        
        header.addSpacing(16)
        
        # Clock
        clock_col = QVBoxLayout()
        clock_col.setSpacing(0)
        self.time_label = QLabel("--:--")
        self.time_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self.time_label)
        self.date_label = QLabel("---")
        self.date_label.setStyleSheet(f"font-size: 10px; color: {self.t['text3']};")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self.date_label)
        header.addLayout(clock_col)
        
        main.addWidget(self.header_frame)
        
        # ═══════════════════ BODY ═══════════════════
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        
        # ─────────── LEFT: Main Content ───────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(24, 20, 16, 20)
        left_layout.setSpacing(16)
        
        # Search
        self.search = QLineEdit()
        self.search.setPlaceholderText("Describe what photos to find...")
        self.search.setText("Screenshots of banking apps, payment confirmations, Venmo/Zelle/CashApp transactions, credit card statements, and text/message conversations")
        left_layout.addWidget(self.search)
        
        # Controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(12)
        
        lbl = QLabel("Scan:")
        lbl.setStyleSheet(f"color: {self.t['text2']}; font-size: 13px;")
        ctrl.addWidget(lbl)
        
        self.limit_input = QSpinBox()
        self.limit_input.setRange(0, 500000)
        self.limit_input.setValue(100)
        self.limit_input.setSpecialValueText("All")
        self.limit_input.setFixedWidth(90)
        ctrl.addWidget(self.limit_input)
        
        photos_lbl = QLabel("photos")
        photos_lbl.setStyleSheet(f"color: {self.t['text3']}; font-size: 12px;")
        ctrl.addWidget(photos_lbl)
        
        ctrl.addStretch()
        
        self.start_btn = QPushButton("Start Scan")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_scan)
        ctrl.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        ctrl.addWidget(self.stop_btn)
        
        left_layout.addLayout(ctrl)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {self.t['surface2']}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {self.t['accent']}; border-radius: 2px; }}
        """)
        self.progress_bar.setValue(0)
        left_layout.addWidget(self.progress_bar)
        
        # Status row
        status_row = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color: {self.t['text3']}; font-size: 12px;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {self.t['text2']}; font-size: 12px;")
        status_row.addWidget(self.progress_label)
        left_layout.addLayout(status_row)
        
        # Preview
        self.preview_frame = QFrame()
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(16, 16, 16, 16)
        
        self.photo_label = QLabel()
        self.photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_label.setMinimumHeight(300)
        self.photo_label.setStyleSheet(f"color: {self.t['text3']}; font-size: 48px;")
        self.photo_label.setText("◎")
        preview_layout.addWidget(self.photo_label, 1)
        
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.name_label)
        
        self.reason_label = QLabel("")
        self.reason_label.setStyleSheet(f"color: {self.t['text2']}; font-size: 12px;")
        self.reason_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reason_label.setWordWrap(True)
        preview_layout.addWidget(self.reason_label)
        
        self.match_badge = QLabel("")
        self.match_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.match_badge.hide()
        preview_layout.addWidget(self.match_badge)
        
        left_layout.addWidget(self.preview_frame, 1)
        
        # Stats
        stats = QHBoxLayout()
        stats.setSpacing(12)
        
        self.stat_scanned = self.make_stat("0", "SCANNED", self.t['accent'])
        self.stat_matches = self.make_stat("0", "MATCHES", self.t['green'])
        self.stat_cost = self.make_stat("$0.00", "COST", self.t['orange'])
        
        stats.addWidget(self.stat_scanned)
        stats.addWidget(self.stat_matches)
        stats.addWidget(self.stat_cost)
        
        left_layout.addLayout(stats)
        
        body.addWidget(left, 3)
        
        # ─────────── RIGHT: Sidebar ───────────
        self.sidebar = QFrame()
        self.sidebar.setMinimumWidth(300)
        self.sidebar.setMaximumWidth(400)
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(16, 20, 16, 20)
        sb_layout.setSpacing(12)
        
        # Header
        sb_header = QHBoxLayout()
        sb_title = QLabel("Matches")
        sb_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        sb_header.addWidget(sb_title)
        
        self.match_count = QLabel("0")
        self.match_count.setStyleSheet(f"""
            background: {self.t['green']};
            color: white;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 8px;
        """)
        sb_header.addWidget(self.match_count)
        sb_header.addStretch()
        sb_layout.addLayout(sb_header)
        
        # List
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        container = QWidget()
        self.matches_list = QVBoxLayout(container)
        self.matches_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matches_list.setSpacing(8)
        scroll.setWidget(container)
        sb_layout.addWidget(scroll, 1)
        
        # Open button
        open_btn = QPushButton("Open Photos App")
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.t['surface2']};
                border: none;
                border-radius: 10px;
                padding: 12px;
                color: {self.t['text']};
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {self.t['surface3']}; }}
        """)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: subprocess.run(["open", "-a", "Photos"]))
        sb_layout.addWidget(open_btn)
        
        body.addWidget(self.sidebar)
        
        main.addLayout(body, 1)
    
    def make_stat(self, value, label, color):
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {self.t['surface']};
                border: none;
                border-radius: 12px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        
        val = QLabel(value)
        val.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {color};")
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setObjectName("value")
        layout.addWidget(val)
        
        lbl = QLabel(label)
        lbl.setStyleSheet(f"font-size: 10px; color: {self.t['text3']}; letter-spacing: 1px;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        
        return frame
    
    def update_stat(self, frame, value):
        frame.findChild(QLabel, "value").setText(str(value))
    
    def make_match_card(self, data):
        t = self.t
        conf = int(data.get("confidence", 0) * 100)
        
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {t['green_bg']};
                border: none;
                border-radius: 10px;
            }}
        """)
        
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # Thumb
        thumb = QLabel()
        thumb.setFixedSize(44, 44)
        thumb.setStyleSheet(f"border-radius: 8px; background: {t['surface2']}; border: none;")
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((44, 44))
                buf = BytesIO()
                img.save(buf, format='JPEG')
                qimg = QImage.fromData(buf.getvalue())
                thumb.setPixmap(QPixmap.fromImage(qimg).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        except:
            thumb.setText("·")
        layout.addWidget(thumb)
        
        # Info - takes remaining space
        info = QVBoxLayout()
        info.setSpacing(2)
        
        # Truncate filename smartly
        filename = data.get('filename', '')
        if len(filename) > 28:
            filename = filename[:12] + "..." + filename[-12:]
        name = QLabel(filename)
        name.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {t['text']}; border: none;")
        info.addWidget(name)
        
        reason = QLabel(data.get('reason', '')[:40])
        reason.setStyleSheet(f"font-size: 10px; color: {t['text3']}; border: none;")
        reason.setWordWrap(True)
        info.addWidget(reason)
        layout.addLayout(info, 1)
        
        # Badge - fixed width so it doesn't get cut off
        badge = QLabel(f"{conf}%")
        badge.setFixedWidth(45)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"background: {t['green']}; color: white; font-size: 11px; font-weight: 700; padding: 4px 0; border-radius: 8px; border: none;")
        layout.addWidget(badge)
        
        return card
    
    def refresh_matches(self):
        while self.matches_list.count():
            item = self.matches_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for m in self.matches:
            self.matches_list.addWidget(self.make_match_card(m))
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.scale_ui()
        if self._pixmap:
            self.scale_preview()
    
    def scale_ui(self):
        """Scale all UI elements based on window size."""
        w = self.width()
        h = self.height()
        scale = min(w / 1200, h / 800)  # Base size 1200x800
        scale = max(0.7, min(scale, 1.5))  # Clamp between 0.7 and 1.5
        
        # Font sizes
        title_size = int(15 * scale)
        normal_size = int(13 * scale)
        small_size = int(11 * scale)
        tiny_size = int(10 * scale)
        stat_size = int(24 * scale)
        time_size = int(14 * scale)
        
        # Padding/margins
        pad = int(12 * scale)
        radius = int(10 * scale)
        
        t = self.t
        
        # Update header elements
        if hasattr(self, 'header_frame'):
            for child in self.header_frame.findChildren(QLabel):
                text = child.text()
                if "Photo Cleaner" in text:
                    child.setStyleSheet(f"font-size: {title_size}px; font-weight: 600;")
                elif "photos in library" in text or "Counting" in text:
                    child.setStyleSheet(f"color: {t['text2']}; font-size: {small_size}px;")
                elif "API" in text or "No API" in text:
                    child.setStyleSheet(f"color: {t['text3']}; font-size: {tiny_size}px; margin-left: 4px;")
        
        # Time
        if hasattr(self, 'time_label'):
            self.time_label.setStyleSheet(f"font-size: {time_size}px; font-weight: 600;")
        if hasattr(self, 'date_label'):
            self.date_label.setStyleSheet(f"font-size: {tiny_size}px; color: {t['text3']};")
        
        # Search input
        if hasattr(self, 'search'):
            self.search.setStyleSheet(f"""
                background: {t['surface2']};
                border: none;
                border-radius: {radius}px;
                padding: {pad}px {int(pad*1.2)}px;
                color: {t['text']};
                font-size: {normal_size}px;
            """)
        
        # Buttons
        if hasattr(self, 'start_btn'):
            self.start_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['accent']};
                    border: none;
                    border-radius: {radius}px;
                    padding: {pad}px {int(pad*2)}px;
                    color: white;
                    font-weight: 600;
                    font-size: {normal_size}px;
                }}
                QPushButton:hover {{ background: {t['accent2']}; }}
                QPushButton:disabled {{ background: {t['surface2']}; color: {t['text3']}; }}
            """)
        
        if hasattr(self, 'stop_btn'):
            self.stop_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(239, 68, 68, 0.15);
                    border: none;
                    border-radius: {radius}px;
                    padding: {pad}px {int(pad*1.8)}px;
                    color: {t['red']};
                    font-weight: 600;
                    font-size: {normal_size}px;
                }}
                QPushButton:hover {{ background: rgba(239, 68, 68, 0.25); }}
            """)
        
        if hasattr(self, 'theme_btn'):
            self.theme_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['surface2']};
                    border: none;
                    border-radius: {int(radius*0.8)}px;
                    padding: {int(pad*0.6)}px {pad}px;
                    color: {t['text']};
                    font-size: {time_size}px;
                }}
                QPushButton:hover {{ background: {t['surface3']}; }}
            """)
        
        # Status labels
        if hasattr(self, 'status_label'):
            self.status_label.setStyleSheet(f"color: {t['text3']}; font-size: {small_size}px;")
        if hasattr(self, 'progress_label'):
            self.progress_label.setStyleSheet(f"color: {t['text2']}; font-size: {small_size}px;")
        
        # Photo info
        if hasattr(self, 'name_label'):
            self.name_label.setStyleSheet(f"font-size: {normal_size}px; font-weight: 500;")
        if hasattr(self, 'reason_label'):
            self.reason_label.setStyleSheet(f"color: {t['text2']}; font-size: {small_size}px;")
        
        # Stats
        for stat_frame in [getattr(self, 'stat_scanned', None), getattr(self, 'stat_matches', None), getattr(self, 'stat_cost', None)]:
            if stat_frame:
                stat_frame.setStyleSheet(f"""
                    QFrame {{
                        background: {t['surface']};
                        border: none;
                        border-radius: {radius}px;
                    }}
                """)
                value_label = stat_frame.findChild(QLabel, "value")
                if value_label:
                    color = value_label.styleSheet().split("color:")[1].split(";")[0].strip() if "color:" in value_label.styleSheet() else t['accent']
                    value_label.setStyleSheet(f"font-size: {stat_size}px; font-weight: 700; color: {color};")
                for lbl in stat_frame.findChildren(QLabel):
                    if lbl.objectName() != "value":
                        lbl.setStyleSheet(f"font-size: {tiny_size}px; color: {t['text3']}; letter-spacing: 1px;")
        
        # Sidebar - use min/max instead of fixed
        sidebar_min = int(280 * scale)
        sidebar_max = int(380 * scale)
        if hasattr(self, 'sidebar'):
            self.sidebar.setMinimumWidth(max(260, sidebar_min))
            self.sidebar.setMaximumWidth(max(320, sidebar_max))
        
        # Match count badge
        if hasattr(self, 'match_count'):
            self.match_count.setStyleSheet(f"""
                background: {t['green']};
                color: white;
                font-size: {tiny_size}px;
                font-weight: 600;
                padding: {int(pad*0.2)}px {int(pad*0.7)}px;
                border-radius: {int(radius*0.8)}px;
            """)
    
    def scale_preview(self):
        if self._pixmap:
            size = self.photo_label.size()
            scaled = self._pixmap.scaled(size.width() - 20, size.height() - 20, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.photo_label.setPixmap(scaled)
    
    def start_scan(self):
        desc = self.search.text().strip()
        if not desc:
            return
        
        self.matches = []
        self._pixmap = None
        self.refresh_matches()
        self.match_count.setText("0")
        self.update_stat(self.stat_scanned, "0")
        self.update_stat(self.stat_matches, "0")
        self.update_stat(self.stat_cost, "$0.00")
        self.progress_bar.setValue(0)
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        limit = self.limit_input.value() or None
        scan_count = limit if limit else self.total_photos
        self.status_label.setText(f"Scanning {scan_count:,} photos...")
        
        self.scanner = ScannerThread(desc, limit)
        self.scanner.progress.connect(self.on_progress)
        self.scanner.photo_scanned.connect(self.on_photo)
        self.scanner.finished_scan.connect(self.on_finished)
        self.scanner.error.connect(lambda e: QMessageBox.warning(self, "Error", e))
        self.scanner.start()
    
    def stop_scan(self):
        if self.scanner:
            self.scanner.stop()
    
    def on_progress(self, cur, total, speed, eta):
        pct = int(cur / total * 100) if total else 0
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(cur)
        
        # Format ETA
        if eta > 3600:
            eta_str = f"{eta // 3600}h {(eta % 3600) // 60}m"
        elif eta > 60:
            eta_str = f"{eta // 60}m {eta % 60}s"
        else:
            eta_str = f"{eta}s"
        
        self.progress_label.setText(f"{cur:,} / {total:,}  •  {speed:.1f}/sec  •  {eta_str} left")
    
    def on_photo(self, data):
        self.update_stat(self.stat_scanned, f"{self.scanner.stats['scanned']:,}")
        self.update_stat(self.stat_matches, f"{self.scanner.stats['matched']:,}")
        self.update_stat(self.stat_cost, f"${self.scanner.stats['cost']:.4f}")
        
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((600, 600))
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=90)
                qimg = QImage.fromData(buf.getvalue())
                self._pixmap = QPixmap.fromImage(qimg)
                self.scale_preview()
        except:
            pass
        
        self.name_label.setText(data.get('filename', '')[:50])
        self.reason_label.setText(data.get('reason', ''))
        
        if data.get('is_match'):
            conf = int(data.get('confidence', 0) * 100)
            self.match_badge.setText(f"✓ Match · {conf}%")
            self.match_badge.setStyleSheet(f"""
                background: {self.t['green_bg']};
                color: {self.t['green']};
                font-size: 13px;
                font-weight: 600;
                padding: 8px 20px;
                border-radius: 16px;
            """)
            self.match_badge.show()
            
            self.matches.append(data)
            self.match_count.setText(str(len(self.matches)))
            self.matches_list.insertWidget(0, self.make_match_card(data))
            
            # Add to album in real-time
            add_to_album(data.get('filename', ''), data.get('uuid', ''))
        else:
            self.match_badge.hide()
    
    def on_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText(f"Done · {stats['matched']} matches")
        self.progress_label.setText("")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
