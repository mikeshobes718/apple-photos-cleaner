#!/usr/bin/env python3
"""
Photo Cleaner Pro - Premium Desktop App
AI-powered photo organization with elegant UI.
"""

import os
import sys
import json
import base64
import subprocess
from io import BytesIO

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
ALBUM_NAME = "🤖 AI Matches - To Delete"
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
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QSettings, QTimer
    from PyQt6.QtGui import QPixmap, QImage, QPalette, QColor, QFont, QAction, QIcon
except ImportError:
    print("PyQt6 required: pip install PyQt6")
    sys.exit(1)

try:
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except:
        pass
except ImportError:
    print("Pillow required: pip install pillow")
    sys.exit(1)

try:
    import openai
except ImportError:
    print("OpenAI required: pip install openai")
    sys.exit(1)


# ============================================================
# Theme System
# ============================================================
class Theme:
    DARK = {
        "name": "dark",
        "bg_primary": "#09090b",
        "bg_secondary": "#18181b",
        "bg_tertiary": "#27272a",
        "bg_card": "rgba(255,255,255,0.03)",
        "border": "rgba(255,255,255,0.08)",
        "border_hover": "rgba(255,255,255,0.15)",
        "text_primary": "#fafafa",
        "text_secondary": "#a1a1aa",
        "text_muted": "#71717a",
        "accent": "#8b5cf6",
        "accent_hover": "#7c3aed",
        "success": "#22c55e",
        "success_bg": "rgba(34, 197, 94, 0.1)",
        "warning": "#f59e0b",
        "danger": "#ef4444",
        "shadow": "rgba(0,0,0,0.5)",
    }
    
    LIGHT = {
        "name": "light",
        "bg_primary": "#ffffff",
        "bg_secondary": "#f4f4f5",
        "bg_tertiary": "#e4e4e7",
        "bg_card": "rgba(0,0,0,0.02)",
        "border": "rgba(0,0,0,0.08)",
        "border_hover": "rgba(0,0,0,0.15)",
        "text_primary": "#18181b",
        "text_secondary": "#52525b",
        "text_muted": "#a1a1aa",
        "accent": "#7c3aed",
        "accent_hover": "#6d28d9",
        "success": "#16a34a",
        "success_bg": "rgba(22, 163, 74, 0.1)",
        "warning": "#d97706",
        "danger": "#dc2626",
        "shadow": "rgba(0,0,0,0.1)",
    }


def get_stylesheet(t):
    return f"""
    QMainWindow {{
        background: {t['bg_primary']};
    }}
    QWidget {{
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
    }}
    QLabel {{
        color: {t['text_primary']};
        background: transparent;
    }}
    QLineEdit {{
        background: {t['bg_secondary']};
        border: 1px solid {t['border']};
        border-radius: 10px;
        padding: 14px 16px;
        color: {t['text_primary']};
        font-size: 14px;
        selection-background-color: {t['accent']};
    }}
    QLineEdit:focus {{
        border-color: {t['accent']};
    }}
    QSpinBox {{
        background: {t['bg_secondary']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 8px 12px;
        color: {t['text_primary']};
        font-size: 13px;
    }}
    QCheckBox {{
        color: {t['text_secondary']};
        spacing: 8px;
        font-size: 13px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 5px;
        border: 2px solid {t['border']};
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        background: {t['accent']};
        border-color: {t['accent']};
    }}
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 6px;
        margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t['border']};
        border-radius: 3px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {t['text_muted']};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QMenuBar {{
        background: {t['bg_primary']};
        color: {t['text_primary']};
        border-bottom: 1px solid {t['border']};
        padding: 4px 8px;
    }}
    QMenuBar::item:selected {{
        background: {t['bg_tertiary']};
        border-radius: 4px;
    }}
    QMenu {{
        background: {t['bg_secondary']};
        border: 1px solid {t['border']};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 8px 24px;
        border-radius: 4px;
        color: {t['text_primary']};
    }}
    QMenu::item:selected {{
        background: {t['accent']};
        color: white;
    }}
    QMenu::separator {{
        height: 1px;
        background: {t['border']};
        margin: 4px 8px;
    }}
    """


# ============================================================
# Scanner Thread
# ============================================================
class ScannerThread(QThread):
    progress = pyqtSignal(int, int)
    photo_scanned = pyqtSignal(dict)
    finished_scan = pyqtSignal(dict)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, description, limit=None, dry_run=False):
        super().__init__()
        self.description = description
        self.limit = limit
        self.dry_run = dry_run
        self.running = True
        self.stats = {"scanned": 0, "matched": 0, "cost": 0.0}
        self.matches = []
    
    def stop(self):
        self.running = False
    
    def encode_photo(self, path):
        try:
            with Image.open(path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=85)
                return base64.b64encode(buf.getvalue()).decode(), None
        except Exception as e:
            return None, str(e)
    
    def analyze(self, img_data):
        try:
            client = openai.OpenAI(api_key=load_api_key())
            r = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": 'Analyze if image matches. Return JSON: {"match": true/false, "confidence": 0.0-1.0, "reason": "brief"}'},
                    {"role": "user", "content": [
                        {"type": "text", "text": f'Match: "{self.description}"?'},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}", "detail": "low"}}
                    ]}
                ],
                max_tokens=100, timeout=30
            )
            content = r.choices[0].message.content.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            return json.loads(content)
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": str(e)[:50]}
    
    def run(self):
        if not load_api_key():
            self.error.emit("No API key found")
            return
        
        self.status.emit("Finding photos...")
        photos = get_local_photos(self.limit)
        if not photos:
            self.error.emit("No photos found")
            return
        
        total = len(photos)
        for i, p in enumerate(photos):
            if not self.running:
                break
            
            self.progress.emit(i + 1, total)
            img_data, err = self.encode_photo(p['path'])
            if err:
                continue
            
            result = self.analyze(img_data)
            self.stats["scanned"] += 1
            self.stats["cost"] += 0.00015
            
            is_match = result.get("match") and result.get("confidence", 0) >= CONFIDENCE_THRESHOLD
            data = {**p, "is_match": is_match, "confidence": result.get("confidence", 0), "reason": result.get("reason", "")[:100]}
            
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
        self.settings = QSettings("PhotoCleanerPro", "PhotoCleanerPro")
        self.scanner = None
        self.matches = []
        
        # Determine initial theme
        self.is_dark = self.settings.value("dark_mode", self.system_is_dark(), type=bool)
        self.theme = Theme.DARK if self.is_dark else Theme.LIGHT
        
        self.setup_menu()
        self.setup_ui()
        self.apply_theme()
    
    def system_is_dark(self):
        """Check if system is in dark mode."""
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True
            )
            return result.stdout.strip().lower() == "dark"
        except:
            return True
    
    def setup_menu(self):
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        restart = QAction("Restart App", self)
        restart.setShortcut("Ctrl+R")
        restart.triggered.connect(self.restart_app)
        file_menu.addAction(restart)
        
        clear = QAction("Clear Results", self)
        clear.setShortcut("Ctrl+Shift+C")
        clear.triggered.connect(self.clear_results)
        file_menu.addAction(clear)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        self.theme_action = QAction("Switch to Light Mode" if self.is_dark else "Switch to Dark Mode", self)
        self.theme_action.setShortcut("Ctrl+T")
        self.theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.theme_action)
        
        view_menu.addSeparator()
        
        system_theme = QAction("Use System Theme", self)
        system_theme.triggered.connect(self.use_system_theme)
        view_menu.addAction(system_theme)
    
    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.theme = Theme.DARK if self.is_dark else Theme.LIGHT
        self.settings.setValue("dark_mode", self.is_dark)
        self.theme_action.setText("Switch to Light Mode" if self.is_dark else "Switch to Dark Mode")
        self.apply_theme()
    
    def use_system_theme(self):
        self.is_dark = self.system_is_dark()
        self.theme = Theme.DARK if self.is_dark else Theme.LIGHT
        self.settings.setValue("dark_mode", self.is_dark)
        self.theme_action.setText("Switch to Light Mode" if self.is_dark else "Switch to Dark Mode")
        self.apply_theme()
    
    def apply_theme(self):
        t = self.theme
        self.setStyleSheet(get_stylesheet(t))
        
        # Update dynamic elements
        if hasattr(self, 'photo_preview'):
            self.photo_preview.setStyleSheet(f"""
                background: {t['bg_secondary']};
                border: 1px solid {t['border']};
                border-radius: 16px;
            """)
        
        if hasattr(self, 'start_btn'):
            self.start_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['accent']};
                    border: none;
                    border-radius: 10px;
                    padding: 12px 28px;
                    color: white;
                    font-size: 14px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background: {t['accent_hover']}; }}
                QPushButton:disabled {{ background: {t['bg_tertiary']}; color: {t['text_muted']}; }}
            """)
        
        if hasattr(self, 'stop_btn'):
            self.stop_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: 1px solid {t['danger']};
                    border-radius: 10px;
                    padding: 12px 20px;
                    color: {t['danger']};
                    font-size: 14px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background: rgba(239, 68, 68, 0.1); }}
            """)
        
        if hasattr(self, 'matches_panel'):
            self.matches_panel.setStyleSheet(f"""
                QFrame {{
                    background: {t['bg_secondary']};
                    border-left: 1px solid {t['border']};
                }}
            """)
        
        # Update stat cards
        for attr in ['stat_scanned', 'stat_matches', 'stat_cost']:
            if hasattr(self, attr):
                getattr(self, attr).update_theme(t)
        
        # Update match cards
        self.refresh_matches()
    
    def refresh_matches(self):
        if not hasattr(self, 'matches_list'):
            return
        # Clear and re-add matches with current theme
        while self.matches_list.count():
            item = self.matches_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for m in self.matches:
            self.matches_list.addWidget(self.create_match_card(m))
    
    def create_match_card(self, data):
        t = self.theme
        conf = int(data.get("confidence", 0) * 100)
        
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {t['success_bg']};
                border: 1px solid {t['success']};
                border-radius: 12px;
                padding: 4px;
            }}
        """)
        
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)
        
        # Thumbnail
        thumb = QLabel()
        thumb.setFixedSize(48, 48)
        thumb.setStyleSheet(f"border-radius: 8px; background: {t['bg_tertiary']}; border: none;")
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((48, 48))
                buf = BytesIO()
                img.save(buf, format='JPEG')
                qimg = QImage.fromData(buf.getvalue())
                thumb.setPixmap(QPixmap.fromImage(qimg).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        except:
            thumb.setText("📷")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(thumb)
        
        # Info
        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(data.get('filename', '')[:25])
        name.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {t['text_primary']}; border: none;")
        info.addWidget(name)
        reason = QLabel(data.get('reason', '')[:40])
        reason.setStyleSheet(f"font-size: 11px; color: {t['text_muted']}; border: none;")
        info.addWidget(reason)
        layout.addLayout(info, 1)
        
        # Badge
        badge = QLabel(f"{conf}%")
        badge.setStyleSheet(f"""
            background: {t['success']};
            color: white;
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 8px;
            border: none;
        """)
        layout.addWidget(badge)
        
        return card
    
    def setup_ui(self):
        self.setWindowTitle("Photo Cleaner Pro")
        self.setMinimumSize(900, 600)
        self.resize(1200, 800)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ==================== MAIN CONTENT ====================
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(32, 24, 24, 24)
        main_layout.setSpacing(20)
        
        # Header
        header = QHBoxLayout()
        
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        
        title = QLabel("Photo Cleaner Pro")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title_col.addWidget(title)
        
        subtitle = QLabel("AI-powered photo organization")
        subtitle.setStyleSheet(f"font-size: 13px; color: {self.theme['text_muted']};")
        title_col.addWidget(subtitle)
        header.addLayout(title_col)
        
        header.addStretch()
        
        # API indicator
        api_key = load_api_key()
        api_dot = QLabel("●" if api_key else "○")
        api_dot.setStyleSheet(f"font-size: 10px; color: {self.theme['success'] if api_key else self.theme['danger']};")
        header.addWidget(api_dot)
        
        api_text = QLabel("API Ready" if api_key else "No API Key")
        api_text.setStyleSheet(f"font-size: 12px; color: {self.theme['text_muted']}; margin-left: 4px;")
        header.addWidget(api_text)
        
        main_layout.addLayout(header)
        
        # Search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("What photos should I find? (e.g., banking screenshots, receipts, memes...)")
        self.search_input.setText("banking, payments, and messaging screenshots")
        main_layout.addWidget(self.search_input)
        
        # Controls
        controls = QHBoxLayout()
        controls.setSpacing(16)
        
        limit_lbl = QLabel("Limit:")
        limit_lbl.setStyleSheet(f"color: {self.theme['text_muted']}; font-size: 13px;")
        controls.addWidget(limit_lbl)
        
        self.limit_input = QSpinBox()
        self.limit_input.setRange(0, 200000)
        self.limit_input.setValue(100)
        self.limit_input.setSpecialValueText("All")
        self.limit_input.setFixedWidth(100)
        controls.addWidget(self.limit_input)
        
        self.dry_run = QCheckBox("Preview only")
        controls.addWidget(self.dry_run)
        
        controls.addStretch()
        
        self.start_btn = QPushButton("Start Scan")
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_scan)
        controls.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        controls.addWidget(self.stop_btn)
        
        main_layout.addLayout(controls)
        
        # Preview area (responsive)
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(12)
        
        # Status bar
        status_row = QHBoxLayout()
        self.status_label = QLabel("Ready to scan")
        self.status_label.setStyleSheet(f"font-size: 13px; color: {self.theme['text_muted']};")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"font-size: 13px; color: {self.theme['text_secondary']};")
        status_row.addWidget(self.progress_label)
        preview_layout.addLayout(status_row)
        
        # Photo preview - stretches with window
        self.photo_preview = QLabel()
        self.photo_preview.setMinimumSize(300, 300)
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setScaledContents(False)
        self.photo_preview.setText("📷")
        self.photo_preview.setStyleSheet(f"""
            font-size: 80px;
            color: {self.theme['text_muted']};
            background: {self.theme['bg_secondary']};
            border: 1px solid {self.theme['border']};
            border-radius: 16px;
        """)
        preview_layout.addWidget(self.photo_preview, 1)
        
        # Photo info
        self.photo_name = QLabel("")
        self.photo_name.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.photo_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.photo_name)
        
        self.photo_reason = QLabel("")
        self.photo_reason.setStyleSheet(f"font-size: 12px; color: {self.theme['text_muted']};")
        self.photo_reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_reason.setWordWrap(True)
        preview_layout.addWidget(self.photo_reason)
        
        # Match badge
        self.match_badge = QLabel("")
        self.match_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.match_badge.hide()
        preview_layout.addWidget(self.match_badge)
        
        main_layout.addWidget(preview_container, 1)
        
        # Stats row
        stats = QHBoxLayout()
        stats.setSpacing(12)
        
        self.stat_scanned = StatCard("0", "Scanned", self.theme['accent'])
        self.stat_matches = StatCard("0", "Matches", self.theme['success'])
        self.stat_cost = StatCard("$0.00", "Cost", self.theme['warning'])
        
        stats.addWidget(self.stat_scanned)
        stats.addWidget(self.stat_matches)
        stats.addWidget(self.stat_cost)
        
        main_layout.addLayout(stats)
        
        layout.addWidget(main, 3)
        
        # ==================== MATCHES PANEL ====================
        self.matches_panel = QFrame()
        matches_layout = QVBoxLayout(self.matches_panel)
        matches_layout.setContentsMargins(20, 24, 20, 24)
        matches_layout.setSpacing(16)
        
        # Header
        mh = QHBoxLayout()
        mh.setSpacing(8)
        mt = QLabel("Matches")
        mt.setStyleSheet("font-size: 16px; font-weight: 700;")
        mh.addWidget(mt)
        
        self.matches_badge = QLabel("0")
        self.matches_badge.setStyleSheet(f"""
            background: {self.theme['success']};
            color: white;
            font-size: 11px;
            font-weight: 700;
            padding: 3px 10px;
            border-radius: 10px;
        """)
        mh.addWidget(self.matches_badge)
        mh.addStretch()
        matches_layout.addLayout(mh)
        
        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        container = QWidget()
        self.matches_list = QVBoxLayout(container)
        self.matches_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matches_list.setSpacing(8)
        scroll.setWidget(container)
        matches_layout.addWidget(scroll, 1)
        
        # Open button
        open_btn = QPushButton("Open Photos App")
        open_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.theme['bg_tertiary']};
                border: 1px solid {self.theme['border']};
                border-radius: 10px;
                padding: 12px;
                color: {self.theme['text_primary']};
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {self.theme['border']}; }}
        """)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: subprocess.run(["open", "-a", "Photos"]))
        matches_layout.addWidget(open_btn)
        
        layout.addWidget(self.matches_panel, 1)
    
    def resizeEvent(self, event):
        """Handle window resize - scale photo preview."""
        super().resizeEvent(event)
        if hasattr(self, '_current_pixmap') and self._current_pixmap:
            self.scale_preview()
    
    def scale_preview(self):
        if hasattr(self, '_current_pixmap') and self._current_pixmap:
            size = self.photo_preview.size()
            scaled = self._current_pixmap.scaled(
                size.width() - 32, size.height() - 32,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.photo_preview.setPixmap(scaled)
    
    def start_scan(self):
        desc = self.search_input.text().strip()
        if not desc:
            QMessageBox.warning(self, "Error", "Enter a description")
            return
        
        self.matches = []
        self._current_pixmap = None
        while self.matches_list.count():
            item = self.matches_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.matches_badge.setText("0")
        self.stat_scanned.set_value("0")
        self.stat_matches.set_value("0")
        self.stat_cost.set_value("$0.00")
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        limit = self.limit_input.value() or None
        self.scanner = ScannerThread(desc, limit, self.dry_run.isChecked())
        self.scanner.progress.connect(self.on_progress)
        self.scanner.photo_scanned.connect(self.on_photo)
        self.scanner.finished_scan.connect(self.on_finished)
        self.scanner.status.connect(lambda s: self.status_label.setText(s))
        self.scanner.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.scanner.start()
    
    def stop_scan(self):
        if self.scanner:
            self.scanner.stop()
    
    def on_progress(self, cur, total):
        pct = int(cur / total * 100) if total else 0
        self.progress_label.setText(f"{cur:,} / {total:,} ({pct}%)")
    
    def on_photo(self, data):
        self.stat_scanned.set_value(f"{self.scanner.stats['scanned']:,}")
        self.stat_matches.set_value(f"{self.scanner.stats['matched']:,}")
        self.stat_cost.set_value(f"${self.scanner.stats['cost']:.4f}")
        
        # Load and display photo
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                # Keep original aspect ratio, max 800px
                img.thumbnail((800, 800), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=90)
                qimg = QImage.fromData(buf.getvalue())
                self._current_pixmap = QPixmap.fromImage(qimg)
                self.scale_preview()
        except:
            pass
        
        self.photo_name.setText(data.get('filename', '')[:50])
        self.photo_reason.setText(data.get('reason', ''))
        
        if data.get('is_match'):
            conf = int(data.get('confidence', 0) * 100)
            self.match_badge.setText(f"✓ Match · {conf}%")
            self.match_badge.setStyleSheet(f"""
                background: {self.theme['success_bg']};
                color: {self.theme['success']};
                font-size: 13px;
                font-weight: 600;
                padding: 8px 20px;
                border-radius: 16px;
            """)
            self.match_badge.show()
            
            self.matches.append(data)
            self.matches_badge.setText(str(len(self.matches)))
            self.matches_list.insertWidget(0, self.create_match_card(data))
        else:
            self.match_badge.hide()
    
    def on_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText(f"Complete · {stats['matched']} matches in {stats['scanned']} photos")
        self.progress_label.setText("")
    
    def restart_app(self):
        if self.scanner and self.scanner.isRunning():
            self.scanner.stop()
            self.scanner.wait()
        QApplication.quit()
        subprocess.Popen([sys.executable] + sys.argv)
    
    def clear_results(self):
        self.matches = []
        self._current_pixmap = None
        while self.matches_list.count():
            item = self.matches_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.matches_badge.setText("0")
        self.stat_scanned.set_value("0")
        self.stat_matches.set_value("0")
        self.stat_cost.set_value("$0.00")
        self.photo_preview.clear()
        self.photo_preview.setText("📷")
        self.photo_name.setText("")
        self.photo_reason.setText("")
        self.match_badge.hide()
        self.status_label.setText("Ready to scan")
        self.progress_label.setText("")


class StatCard(QFrame):
    def __init__(self, value, label, color):
        super().__init__()
        self.color = color
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)
        
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"font-size: 28px; font-weight: 700; color: {color};")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)
        
        self.desc_label = QLabel(label)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.desc_label)
    
    def set_value(self, v):
        self.value_label.setText(v)
    
    def update_theme(self, t):
        self.setStyleSheet(f"""
            QFrame {{
                background: {t['bg_secondary']};
                border: 1px solid {t['border']};
                border-radius: 12px;
            }}
        """)
        self.desc_label.setStyleSheet(f"font-size: 11px; color: {t['text_muted']}; text-transform: uppercase; letter-spacing: 1px;")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Cleaner Pro")
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
