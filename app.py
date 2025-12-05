#!/usr/bin/env python3
"""
Photo Cleaner Pro - Premium Desktop App
"""

import os
import sys
import json
import base64
import subprocess
from io import BytesIO
from datetime import datetime

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7

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
    "bg": "#0a0a0a",
    "surface": "#141414",
    "surface2": "#1c1c1c",
    "border": "#2a2a2a",
    "text": "#e5e5e5",
    "text2": "#888888",
    "text3": "#555555",
    "accent": "#6366f1",
    "accent2": "#4f46e5",
    "green": "#10b981",
    "green_bg": "rgba(16, 185, 129, 0.08)",
    "orange": "#f59e0b",
    "red": "#ef4444",
}

LIGHT = {
    "bg": "#f8f8f8",
    "surface": "#ffffff",
    "surface2": "#f0f0f0",
    "border": "#e0e0e0",
    "text": "#1a1a1a",
    "text2": "#666666",
    "text3": "#999999",
    "accent": "#4f46e5",
    "accent2": "#4338ca",
    "green": "#059669",
    "green_bg": "rgba(5, 150, 105, 0.08)",
    "orange": "#d97706",
    "red": "#dc2626",
}


# ============================================================
# Scanner
# ============================================================
class ScannerThread(QThread):
    progress = pyqtSignal(int, int)
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
        
        for i, p in enumerate(photos):
            if not self.running:
                break
            
            self.progress.emit(i + 1, total)
            
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
        
        self.setup_ui()
        self.apply_theme()
        
        # Clock timer
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()
        
        # Count photos on startup
        QTimer.singleShot(100, self.count_library)
    
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
        self.time_label.setText(now.strftime("%H:%M"))
        self.date_label.setText(now.strftime("%a, %b %d"))
    
    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.t = DARK if self.dark_mode else LIGHT
        self.settings.setValue("dark", self.dark_mode)
        self.theme_btn.setText("☀" if self.dark_mode else "☾")
        self.apply_theme()
    
    def apply_theme(self):
        t = self.t
        self.setStyleSheet(f"""
            QMainWindow {{ background: {t['bg']}; }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            QLineEdit {{
                background: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 8px;
                padding: 12px 14px;
                color: {t['text']};
                font-size: 14px;
            }}
            QLineEdit:focus {{ border-color: {t['accent']}; }}
            QSpinBox {{
                background: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 8px;
                color: {t['text']};
            }}
            QCheckBox {{ color: {t['text2']}; }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border-radius: 4px;
                border: 1px solid {t['border']};
            }}
            QCheckBox::indicator:checked {{
                background: {t['accent']};
                border-color: {t['accent']};
            }}
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: {t['border']}; border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        
        # Update components
        if hasattr(self, 'header_frame'):
            self.header_frame.setStyleSheet(f"background: {t['surface']}; border-bottom: 1px solid {t['border']};")
        
        if hasattr(self, 'preview_frame'):
            self.preview_frame.setStyleSheet(f"""
                background: {t['surface']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            """)
        
        if hasattr(self, 'sidebar'):
            self.sidebar.setStyleSheet(f"background: {t['surface']}; border-left: 1px solid {t['border']};")
        
        if hasattr(self, 'start_btn'):
            self.start_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['accent']};
                    border: none;
                    border-radius: 8px;
                    padding: 10px 24px;
                    color: white;
                    font-weight: 600;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: {t['accent2']}; }}
                QPushButton:disabled {{ background: {t['border']}; color: {t['text3']}; }}
            """)
        
        if hasattr(self, 'stop_btn'):
            self.stop_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {t['red']};
                    border-radius: 8px;
                    padding: 10px 20px;
                    color: {t['red']};
                    font-weight: 600;
                    font-size: 13px;
                }}
                QPushButton:hover {{ background: rgba(239, 68, 68, 0.1); }}
            """)
        
        if hasattr(self, 'theme_btn'):
            self.theme_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['surface2']};
                    border: 1px solid {t['border']};
                    border-radius: 6px;
                    padding: 6px 10px;
                    color: {t['text']};
                    font-size: 14px;
                }}
                QPushButton:hover {{ background: {t['border']}; }}
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
        self.search.setPlaceholderText("What photos to find? (e.g., banking screenshots, receipts...)")
        self.search.setText("banking, payments, and messaging screenshots")
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
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {self.t['border']}; border: none; border-radius: 1px; }}
            QProgressBar::chunk {{ background: {self.t['accent']}; border-radius: 1px; }}
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
        self.sidebar.setFixedWidth(280)
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
                border: 1px solid {self.t['border']};
                border-radius: 8px;
                padding: 10px;
                color: {self.t['text']};
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {self.t['border']}; }}
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
                border: 1px solid {self.t['border']};
                border-radius: 10px;
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
                border: 1px solid {t['green']};
                border-radius: 8px;
            }}
        """)
        
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        
        # Thumb
        thumb = QLabel()
        thumb.setFixedSize(40, 40)
        thumb.setStyleSheet(f"border-radius: 6px; background: {t['surface2']}; border: none;")
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((40, 40))
                buf = BytesIO()
                img.save(buf, format='JPEG')
                qimg = QImage.fromData(buf.getvalue())
                thumb.setPixmap(QPixmap.fromImage(qimg).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        except:
            thumb.setText("·")
        layout.addWidget(thumb)
        
        # Info
        info = QVBoxLayout()
        info.setSpacing(1)
        name = QLabel(data.get('filename', '')[:20])
        name.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {t['text']}; border: none;")
        info.addWidget(name)
        reason = QLabel(data.get('reason', '')[:30])
        reason.setStyleSheet(f"font-size: 10px; color: {t['text3']}; border: none;")
        info.addWidget(reason)
        layout.addLayout(info, 1)
        
        # Badge
        badge = QLabel(f"{conf}%")
        badge.setStyleSheet(f"background: {t['green']}; color: white; font-size: 10px; font-weight: 700; padding: 3px 7px; border-radius: 6px; border: none;")
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
        if self._pixmap:
            self.scale_preview()
    
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
    
    def on_progress(self, cur, total):
        pct = int(cur / total * 100) if total else 0
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(cur)
        self.progress_label.setText(f"{cur:,} / {total:,}")
    
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
            self.match_badge.setText(f"Match · {conf}%")
            self.match_badge.setStyleSheet(f"""
                background: {self.t['green_bg']};
                color: {self.t['green']};
                font-size: 12px;
                font-weight: 600;
                padding: 6px 16px;
                border-radius: 12px;
            """)
            self.match_badge.show()
            
            self.matches.append(data)
            self.match_count.setText(str(len(self.matches)))
            self.matches_list.insertWidget(0, self.make_match_card(data))
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
