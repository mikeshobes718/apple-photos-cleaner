#!/usr/bin/env python3
"""
Photo Cleaner Pro - Desktop App
Native macOS application for AI-powered photo organization.
Uses direct SQLite access instead of osxphotos for better bundling.
"""

import os
import sys
import json
import time
import base64
import sqlite3
import subprocess
from pathlib import Path
from io import BytesIO
from datetime import datetime

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
ALBUM_NAME = "🤖 AI Matches - To Delete"
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7

PHOTOS_DB = os.path.expanduser("~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite")
ORIGINALS_PATH = os.path.expanduser("~/Pictures/Photos Library.photoslibrary/originals")

SKIP_EXTENSIONS = {'.mov', '.mp4', '.m4v', '.avi', '.3gp', '.mkv', '.webm',
                   '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2'}

# ============================================================
# Load API Key
# ============================================================
def load_api_key():
    if not os.path.exists(ENV_PATH):
        return None
    try:
        with open(ENV_PATH) as f:
            data = json.load(f)
        return data.get("OPENAI_API_KEY")
    except:
        return None

# ============================================================
# Get Photos from Library
# ============================================================
def get_local_photos(limit=None):
    """Get photos directly from filesystem (faster than osxphotos)."""
    photos = []
    
    if not os.path.exists(ORIGINALS_PATH):
        return photos
    
    for root, dirs, files in os.walk(ORIGINALS_PATH):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            if ext in SKIP_EXTENSIONS:
                continue
            if ext in ['.jpg', '.jpeg', '.png', '.heic', '.webp', '.gif', '.tiff']:
                filepath = os.path.join(root, filename)
                photos.append({
                    'filename': filename,
                    'path': filepath,
                    'uuid': os.path.splitext(filename)[0]  # UUID is filename without ext
                })
                
                if limit and len(photos) >= limit:
                    return photos
    
    return photos

# ============================================================
# PyQt6 Application
# ============================================================
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QLineEdit, QSpinBox, QCheckBox, QProgressBar,
        QScrollArea, QFrame, QSplitter, QMessageBox
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt6.QtGui import QPixmap, QImage, QPalette, QColor
except ImportError:
    print("❌ PyQt6 not installed. Run: pip install PyQt6")
    sys.exit(1)

try:
    from PIL import Image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except:
        pass  # HEIC support optional
except ImportError:
    print("❌ Pillow not installed. Run: pip install pillow")
    sys.exit(1)

try:
    import openai
except ImportError:
    print("❌ OpenAI not installed. Run: pip install openai")
    sys.exit(1)


# ============================================================
# Thumbnail Cache
# ============================================================
class ThumbnailCache:
    def __init__(self, max_size=500):
        self.cache = {}
        self.max_size = max_size
    
    def get(self, path, size=(80, 80)):
        key = f"{path}_{size}"
        if key in self.cache:
            return self.cache[key]
        
        try:
            with Image.open(path) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail(size, Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)
                
                qimg = QImage.fromData(buffer.read())
                pixmap = QPixmap.fromImage(qimg)
                
                if len(self.cache) >= self.max_size:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = pixmap
                
                return pixmap
        except:
            return None


# ============================================================
# Scanner Thread
# ============================================================
class ScannerThread(QThread):
    progress = pyqtSignal(int, int, str)
    match_found = pyqtSignal(dict)
    scan_item = pyqtSignal(dict)
    finished_scan = pyqtSignal(dict)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, description, limit=None, dry_run=False):
        super().__init__()
        self.description = description
        self.limit = limit
        self.dry_run = dry_run
        self.running = True
        self.client = None
        self.stats = {"scanned": 0, "matched": 0, "skipped": 0, "errors": 0, "cost": 0.0}
    
    def stop(self):
        self.running = False
    
    def encode_photo(self, filepath):
        try:
            with Image.open(filepath) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)
                
                return base64.b64encode(buffer.read()).decode('utf-8'), None
        except Exception as e:
            return None, str(e)
    
    def analyze_photo(self, image_data):
        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": """Analyze if image matches description. Respond with JSON only:
{"match": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}"""
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Does this match: \"{self.description}\"?"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}", "detail": "low"}}
                        ]
                    }
                ],
                max_tokens=150,
                timeout=30
            )
            
            content = response.choices[0].message.content.strip()
            if "```" in content:
                content = content.split("```")[1].replace("json", "").strip()
            
            result = json.loads(content)
            return {
                "match": bool(result.get("match", False)),
                "confidence": float(result.get("confidence", 0)),
                "reason": str(result.get("reason", ""))[:100]
            }
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": f"Error: {str(e)[:50]}"}
    
    def run(self):
        api_key = load_api_key()
        if not api_key:
            self.error.emit("API key not found. Check ~/Documents/Keys/.env")
            return
        
        self.client = openai.OpenAI(api_key=api_key)
        
        self.status.emit("Finding local photos...")
        photos = get_local_photos(self.limit)
        
        if not photos:
            self.error.emit("No local photos found. Check Photos Library path.")
            return
        
        total = len(photos)
        self.status.emit(f"Found {total:,} photos")
        
        matches = []
        cost_per_image = 0.00015
        
        self.status.emit(f"Scanning {total:,} photos...")
        
        for i, photo in enumerate(photos):
            if not self.running:
                break
            
            filename = photo['filename']
            filepath = photo['path']
            self.progress.emit(i + 1, total, filename)
            
            image_data, error = self.encode_photo(filepath)
            
            if error:
                self.stats["errors"] += 1
                continue
            
            result = self.analyze_photo(image_data)
            self.stats["scanned"] += 1
            self.stats["cost"] += cost_per_image
            
            is_match = result["match"] and result["confidence"] >= CONFIDENCE_THRESHOLD
            
            scan_data = {
                "uuid": photo['uuid'],
                "filename": filename,
                "path": filepath,
                "is_match": is_match,
                "confidence": result["confidence"],
                "reason": result["reason"],
                "skipped": False
            }
            
            self.scan_item.emit(scan_data)
            
            if is_match:
                self.stats["matched"] += 1
                matches.append(scan_data)
                self.match_found.emit(scan_data)
        
        self.stats["matches_list"] = matches
        self.finished_scan.emit(self.stats)


# ============================================================
# Photo Item Widget
# ============================================================
class PhotoItem(QFrame):
    def __init__(self, data, thumb_cache, is_match=False):
        super().__init__()
        self.data = data
        self.thumb_cache = thumb_cache
        self.setup_ui(is_match)
    
    def setup_ui(self, is_match):
        self.setStyleSheet(f"""
            QFrame {{
                background: {'rgba(52, 211, 153, 0.15)' if is_match else 'rgba(255, 255, 255, 0.03)'};
                border: 1px solid {'rgba(52, 211, 153, 0.3)' if is_match else 'rgba(255, 255, 255, 0.08)'};
                border-radius: 10px;
                padding: 8px;
                margin: 2px;
            }}
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        
        # Thumbnail
        thumb_label = QLabel()
        thumb_label.setFixedSize(56, 56)
        thumb_label.setStyleSheet("border-radius: 6px; background: rgba(0,0,0,0.3);")
        
        if self.data.get("path"):
            pixmap = self.thumb_cache.get(self.data["path"], (56, 56))
            if pixmap:
                thumb_label.setPixmap(pixmap.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding))
        
        layout.addWidget(thumb_label)
        
        # Info
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)
        
        name_label = QLabel(self.data.get("filename", "Unknown")[:40])
        name_label.setStyleSheet("font-weight: 600; font-size: 13px; color: #f8fafc;")
        info_layout.addWidget(name_label)
        
        reason = self.data.get("reason", "")[:60]
        reason_label = QLabel(reason)
        reason_label.setStyleSheet("font-size: 11px; color: rgba(248, 250, 252, 0.5);")
        info_layout.addWidget(reason_label)
        
        layout.addLayout(info_layout, 1)
        
        # Badge
        if is_match:
            conf = int(self.data.get("confidence", 0) * 100)
            badge = QLabel(f"{conf}%")
            badge.setStyleSheet("background: #34d399; color: white; font-size: 11px; font-weight: 600; padding: 4px 8px; border-radius: 6px;")
            layout.addWidget(badge)


# ============================================================
# Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.thumb_cache = ThumbnailCache()
        self.scanner = None
        self.matches = []
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("Photo Cleaner Pro")
        self.setMinimumSize(1000, 650)
        self.setStyleSheet("""
            QMainWindow { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0c0c0c, stop:0.5 #1a1a2e, stop:1 #16213e); }
            QLabel { color: #f8fafc; }
            QLineEdit, QSpinBox { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 10px; color: #f8fafc; font-size: 14px; }
            QPushButton { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 12px 24px; color: #f8fafc; font-size: 14px; font-weight: 600; }
            QPushButton:hover { background: rgba(255,255,255,0.1); }
            QPushButton#startBtn { background: #818cf8; border: none; }
            QPushButton#startBtn:hover { background: #6366f1; }
            QPushButton#stopBtn { background: rgba(248,113,113,0.2); border-color: rgba(248,113,113,0.3); color: #f87171; }
            QProgressBar { background: rgba(255,255,255,0.1); border: none; border-radius: 5px; height: 10px; }
            QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #818cf8, stop:1 #a78bfa); border-radius: 5px; }
            QScrollArea { border: none; background: transparent; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        
        # Header
        header = QHBoxLayout()
        title = QLabel("🧹 Photo Cleaner Pro")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()
        
        api_key = load_api_key()
        api_status = QLabel("✓ API Connected" if api_key else "✗ No API Key")
        api_status.setStyleSheet(f"background: {'rgba(52,211,153,0.15)' if api_key else 'rgba(248,113,113,0.15)'}; color: {'#34d399' if api_key else '#f87171'}; padding: 8px 16px; border-radius: 8px; font-size: 12px;")
        header.addWidget(api_status)
        main_layout.addLayout(header)
        
        # Controls
        controls = QFrame()
        controls.setStyleSheet("QFrame { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; padding: 16px; }")
        controls_layout = QVBoxLayout(controls)
        
        # Description
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("What photos to find? (e.g., banking screenshots, receipts...)")
        self.desc_input.setText("banking, payments, and messaging screenshots")
        controls_layout.addWidget(self.desc_input)
        
        # Options row
        opts = QHBoxLayout()
        
        opts.addWidget(QLabel("Limit:"))
        self.limit_input = QSpinBox()
        self.limit_input.setRange(0, 100000)
        self.limit_input.setValue(0)
        self.limit_input.setSpecialValueText("All")
        self.limit_input.setFixedWidth(100)
        opts.addWidget(self.limit_input)
        
        self.dry_run_cb = QCheckBox("Dry Run")
        self.dry_run_cb.setStyleSheet("color: rgba(248,250,252,0.7);")
        opts.addWidget(self.dry_run_cb)
        
        opts.addStretch()
        
        self.start_btn = QPushButton("▶ Start Scan")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self.start_scan)
        opts.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        opts.addWidget(self.stop_btn)
        
        controls_layout.addLayout(opts)
        
        # Progress
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-size: 14px;")
        controls_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        controls_layout.addWidget(self.progress_bar)
        
        # Stats
        stats_layout = QHBoxLayout()
        self.stats_labels = {}
        for key, label in [("scanned", "📷 Scanned"), ("matched", "✨ Matches"), ("cost", "💰 Cost")]:
            stat = QVBoxLayout()
            val = QLabel("0" if key != "cost" else "$0.00")
            val.setStyleSheet("font-size: 20px; font-weight: 700; color: #818cf8;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stat.addWidget(val)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 11px; color: rgba(248,250,252,0.5);")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            stat.addWidget(lbl)
            self.stats_labels[key] = val
            stats_layout.addLayout(stat)
        controls_layout.addLayout(stats_layout)
        
        main_layout.addWidget(controls)
        
        # Results
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Matches panel
        matches_frame = QFrame()
        matches_frame.setStyleSheet("QFrame { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; }")
        matches_layout = QVBoxLayout(matches_frame)
        matches_layout.setContentsMargins(12, 12, 12, 12)
        
        mh = QHBoxLayout()
        mh.addWidget(QLabel("🎯 Matches"))
        self.matches_count = QLabel("0")
        self.matches_count.setStyleSheet("background: #818cf8; color: white; padding: 4px 10px; border-radius: 10px; font-size: 11px;")
        mh.addWidget(self.matches_count)
        mh.addStretch()
        matches_layout.addLayout(mh)
        
        self.matches_scroll = QScrollArea()
        self.matches_scroll.setWidgetResizable(True)
        self.matches_container = QWidget()
        self.matches_list = QVBoxLayout(self.matches_container)
        self.matches_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matches_scroll.setWidget(self.matches_container)
        matches_layout.addWidget(self.matches_scroll)
        
        splitter.addWidget(matches_frame)
        
        # Activity panel
        activity_frame = QFrame()
        activity_frame.setStyleSheet("QFrame { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px; }")
        activity_layout = QVBoxLayout(activity_frame)
        activity_layout.setContentsMargins(12, 12, 12, 12)
        
        ah = QHBoxLayout()
        ah.addWidget(QLabel("📜 Activity"))
        self.activity_count = QLabel("0")
        self.activity_count.setStyleSheet("background: #818cf8; color: white; padding: 4px 10px; border-radius: 10px; font-size: 11px;")
        ah.addWidget(self.activity_count)
        ah.addStretch()
        activity_layout.addLayout(ah)
        
        self.activity_scroll = QScrollArea()
        self.activity_scroll.setWidgetResizable(True)
        self.activity_container = QWidget()
        self.activity_list = QVBoxLayout(self.activity_container)
        self.activity_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.activity_scroll.setWidget(self.activity_container)
        activity_layout.addWidget(self.activity_scroll)
        
        splitter.addWidget(activity_frame)
        main_layout.addWidget(splitter, 1)
        
        # Footer
        footer = QHBoxLayout()
        open_btn = QPushButton("📂 Open Album")
        open_btn.clicked.connect(self.open_album)
        footer.addWidget(open_btn)
        footer.addStretch()
        main_layout.addLayout(footer)
    
    def start_scan(self):
        desc = self.desc_input.text().strip()
        if not desc:
            QMessageBox.warning(self, "Error", "Enter a description")
            return
        
        self.matches = []
        self.clear_layout(self.matches_list)
        self.clear_layout(self.activity_list)
        self.matches_count.setText("0")
        self.activity_count.setText("0")
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        limit = self.limit_input.value() or None
        self.scanner = ScannerThread(desc, limit, self.dry_run_cb.isChecked())
        self.scanner.progress.connect(self.on_progress)
        self.scanner.match_found.connect(self.on_match)
        self.scanner.scan_item.connect(self.on_scan)
        self.scanner.finished_scan.connect(self.on_finished)
        self.scanner.status.connect(lambda s: self.status_label.setText(s))
        self.scanner.error.connect(lambda e: QMessageBox.critical(self, "Error", e))
        self.scanner.start()
    
    def stop_scan(self):
        if self.scanner:
            self.scanner.stop()
    
    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def on_progress(self, cur, total, name):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(cur)
        self.status_label.setText(f"Scanning: {name[:40]}...")
    
    def on_match(self, data):
        self.matches.append(data)
        self.matches_count.setText(str(len(self.matches)))
        self.matches_list.insertWidget(0, PhotoItem(data, self.thumb_cache, True))
    
    def on_scan(self, data):
        self.activity_count.setText(str(self.scanner.stats["scanned"]))
        self.stats_labels["scanned"].setText(str(self.scanner.stats["scanned"]))
        self.stats_labels["matched"].setText(str(self.scanner.stats["matched"]))
        self.stats_labels["cost"].setText(f"${self.scanner.stats['cost']:.4f}")
        
        item = PhotoItem(data, self.thumb_cache, data.get("is_match"))
        self.activity_list.insertWidget(0, item)
        if self.activity_list.count() > 50:
            w = self.activity_list.takeAt(50)
            if w.widget():
                w.widget().deleteLater()
    
    def on_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_label.setText(f"✅ Done! {stats.get('matched', 0)} matches in {stats.get('scanned', 0)} photos")
        
        if self.matches and not self.dry_run_cb.isChecked():
            if QMessageBox.question(self, "Add to Album?", f"Add {len(self.matches)} matches to album?") == QMessageBox.StandardButton.Yes:
                self.add_to_album()
    
    def add_to_album(self):
        for m in self.matches:
            script = f'''tell application "Photos"
                try
                    set a to album "{ALBUM_NAME}"
                on error
                    set a to make new album named "{ALBUM_NAME}"
                end try
            end tell'''
            subprocess.run(["osascript", "-e", script], capture_output=True)
        self.open_album()
    
    def open_album(self):
        subprocess.run(["open", "-a", "Photos"])


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(12, 12, 12))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(248, 250, 252))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
