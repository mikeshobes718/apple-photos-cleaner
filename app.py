#!/usr/bin/env python3
"""
Photo Cleaner Pro - Desktop App
Native macOS application for AI-powered photo organization.
"""

import os
import sys
import json
import time
import base64
import subprocess
from pathlib import Path
from io import BytesIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
ALBUM_NAME = "🤖 AI Matches - To Delete"
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7

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
# PyQt6 Application
# ============================================================
try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QLineEdit, QSpinBox, QCheckBox, QProgressBar,
        QScrollArea, QFrame, QSplitter, QMessageBox, QComboBox, QTextEdit
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
    from PyQt6.QtGui import QPixmap, QImage, QFont, QPalette, QColor, QIcon
except ImportError:
    print("❌ PyQt6 not installed. Run: pip install PyQt6")
    sys.exit(1)

try:
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    print("❌ Pillow not installed. Run: pip install pillow pillow-heif")
    sys.exit(1)

try:
    import openai
except ImportError:
    print("❌ OpenAI not installed. Run: pip install openai")
    sys.exit(1)

try:
    import osxphotos
except ImportError:
    print("❌ osxphotos not installed. Run: pip install osxphotos")
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
                
                # Convert to QPixmap
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=85)
                buffer.seek(0)
                
                qimg = QImage.fromData(buffer.read())
                pixmap = QPixmap.fromImage(qimg)
                
                # Cache it
                if len(self.cache) >= self.max_size:
                    # Remove oldest
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = pixmap
                
                return pixmap
        except:
            return None


# ============================================================
# Scanner Thread
# ============================================================
class ScannerThread(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, filename
    match_found = pyqtSignal(dict)  # match data
    scan_item = pyqtSignal(dict)  # each scanned item
    finished_scan = pyqtSignal(dict)  # final stats
    status = pyqtSignal(str)  # status message
    error = pyqtSignal(str)  # error message
    
    def __init__(self, description, limit=None, dry_run=False):
        super().__init__()
        self.description = description
        self.limit = limit
        self.dry_run = dry_run
        self.running = True
        self.client = None
        self.stats = {
            "scanned": 0,
            "matched": 0,
            "skipped": 0,
            "errors": 0,
            "cost": 0.0
        }
    
    def stop(self):
        self.running = False
    
    def encode_photo(self, photo):
        """Encode photo for API."""
        filename = photo.original_filename or "unknown"
        filepath = Path(photo.path)
        
        ext = filepath.suffix.lower()
        if ext in SKIP_EXTENSIONS:
            return None, "skip"
        
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
        """Analyze with OpenAI Vision."""
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
        """Main scan loop."""
        api_key = load_api_key()
        if not api_key:
            self.error.emit("API key not found. Check ~/Documents/Keys/.env")
            return
        
        self.client = openai.OpenAI(api_key=api_key)
        
        # Load Photos library
        self.status.emit("Loading Photos library...")
        try:
            db = osxphotos.PhotosDB()
            all_photos = list(db.photos())
        except Exception as e:
            self.error.emit(f"Failed to load Photos: {e}")
            return
        
        # Filter local photos
        self.status.emit("Finding local photos...")
        photos = [p for p in all_photos if p.path and Path(p.path).exists()]
        
        if not photos:
            self.error.emit("No local photos found. Download from iCloud first.")
            return
        
        self.status.emit(f"Found {len(photos):,} local photos")
        
        # Apply limit
        if self.limit:
            photos = photos[:self.limit]
        
        total = len(photos)
        matches = []
        cost_per_image = 0.00015
        
        self.status.emit(f"Scanning {total:,} photos...")
        
        for i, photo in enumerate(photos):
            if not self.running:
                break
            
            filename = photo.original_filename or f"photo_{i}"
            self.progress.emit(i + 1, total, filename)
            
            # Encode
            image_data, error = self.encode_photo(photo)
            
            if error == "skip":
                self.stats["skipped"] += 1
                self.scan_item.emit({
                    "filename": filename,
                    "path": str(photo.path),
                    "is_match": False,
                    "skipped": True,
                    "reason": "Video/RAW file"
                })
                continue
            elif error:
                self.stats["errors"] += 1
                continue
            
            # Analyze
            result = self.analyze_photo(image_data)
            self.stats["scanned"] += 1
            self.stats["cost"] += cost_per_image
            
            is_match = result["match"] and result["confidence"] >= CONFIDENCE_THRESHOLD
            
            scan_data = {
                "uuid": photo.uuid,
                "filename": filename,
                "path": str(photo.path),
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
        
        # Done
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
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"""
            QFrame {{
                background: {'rgba(52, 211, 153, 0.15)' if is_match else 'rgba(255, 255, 255, 0.03)'};
                border: 1px solid {'rgba(52, 211, 153, 0.3)' if is_match else 'rgba(255, 255, 255, 0.08)'};
                border-radius: 10px;
                padding: 8px;
                margin: 2px;
            }}
            QFrame:hover {{
                background: {'rgba(52, 211, 153, 0.25)' if is_match else 'rgba(255, 255, 255, 0.06)'};
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
        
        reason = self.data.get("reason", "")
        if self.data.get("skipped"):
            reason = "Skipped (video/RAW)"
        reason_label = QLabel(reason[:60])
        reason_label.setStyleSheet("font-size: 11px; color: rgba(248, 250, 252, 0.5);")
        info_layout.addWidget(reason_label)
        
        layout.addLayout(info_layout, 1)
        
        # Badge
        if is_match:
            conf = int(self.data.get("confidence", 0) * 100)
            badge = QLabel(f"{conf}%")
            badge.setStyleSheet("""
                background: #34d399;
                color: white;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
                border-radius: 6px;
            """)
            layout.addWidget(badge)
        elif self.data.get("skipped"):
            badge = QLabel("Skip")
            badge.setStyleSheet("""
                background: rgba(255,255,255,0.1);
                color: rgba(248,250,252,0.5);
                font-size: 10px;
                padding: 4px 8px;
                border-radius: 6px;
            """)
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
        self.recent_scans = []
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("Photo Cleaner Pro")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0c0c0c, stop:0.5 #1a1a2e, stop:1 #16213e);
            }
            QLabel {
                color: #f8fafc;
            }
            QLineEdit, QSpinBox, QComboBox {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 10px 14px;
                color: #f8fafc;
                font-size: 14px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #818cf8;
            }
            QPushButton {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 12px 24px;
                color: #f8fafc;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
            }
            QPushButton:pressed {
                background: rgba(255, 255, 255, 0.15);
            }
            QPushButton#startBtn {
                background: #818cf8;
                border: none;
            }
            QPushButton#startBtn:hover {
                background: #6366f1;
            }
            QPushButton#stopBtn {
                background: rgba(248, 113, 113, 0.2);
                border-color: rgba(248, 113, 113, 0.3);
                color: #f87171;
            }
            QPushButton#stopBtn:hover {
                background: #f87171;
                color: white;
            }
            QProgressBar {
                background: rgba(255, 255, 255, 0.1);
                border: none;
                border-radius: 5px;
                height: 10px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #818cf8, stop:1 #a78bfa);
                border-radius: 5px;
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        
        # Header
        header = QHBoxLayout()
        
        title_layout = QVBoxLayout()
        subtitle = QLabel("AI POWERED")
        subtitle.setStyleSheet("font-size: 11px; color: rgba(248,250,252,0.4); letter-spacing: 2px;")
        title_layout.addWidget(subtitle)
        
        title = QLabel("🧹 Photo Cleaner Pro")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #f8fafc;")
        title_layout.addWidget(title)
        
        header.addLayout(title_layout)
        header.addStretch()
        
        # API Status
        api_key = load_api_key()
        self.api_status = QLabel("✓ API Connected" if api_key else "✗ No API Key")
        self.api_status.setStyleSheet(f"""
            background: {'rgba(52, 211, 153, 0.15)' if api_key else 'rgba(248, 113, 113, 0.15)'};
            color: {'#34d399' if api_key else '#f87171'};
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 500;
        """)
        header.addWidget(self.api_status)
        
        main_layout.addLayout(header)
        
        # Controls Card
        controls_card = QFrame()
        controls_card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
                padding: 20px;
            }
        """)
        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setSpacing(16)
        
        # Description input
        desc_layout = QVBoxLayout()
        desc_label = QLabel("What photos should I find?")
        desc_label.setStyleSheet("font-size: 13px; color: rgba(248,250,252,0.6);")
        desc_layout.addWidget(desc_label)
        
        self.desc_input = QLineEdit()
        self.desc_input.setPlaceholderText("e.g., banking screenshots, payment receipts, messaging conversations...")
        self.desc_input.setText("banking, payments, and messaging screenshots")
        desc_layout.addWidget(self.desc_input)
        controls_layout.addLayout(desc_layout)
        
        # Options row
        options_layout = QHBoxLayout()
        
        # Limit
        limit_layout = QVBoxLayout()
        limit_label = QLabel("Photo Limit")
        limit_label.setStyleSheet("font-size: 12px; color: rgba(248,250,252,0.5);")
        limit_layout.addWidget(limit_label)
        
        self.limit_input = QSpinBox()
        self.limit_input.setRange(0, 100000)
        self.limit_input.setValue(0)
        self.limit_input.setSpecialValueText("All")
        self.limit_input.setFixedWidth(100)
        limit_layout.addWidget(self.limit_input)
        options_layout.addLayout(limit_layout)
        
        # Dry run
        self.dry_run_cb = QCheckBox("Dry Run (preview only)")
        self.dry_run_cb.setStyleSheet("color: rgba(248,250,252,0.7); font-size: 13px;")
        options_layout.addWidget(self.dry_run_cb)
        
        options_layout.addStretch()
        
        # Buttons
        self.start_btn = QPushButton("▶  Start Scan")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self.start_scan)
        options_layout.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        options_layout.addWidget(self.stop_btn)
        
        controls_layout.addLayout(options_layout)
        
        # Progress
        progress_layout = QVBoxLayout()
        
        self.status_label = QLabel("Ready to scan")
        self.status_label.setStyleSheet("font-size: 14px; color: #f8fafc;")
        progress_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        # Stats row
        stats_layout = QHBoxLayout()
        self.stats_labels = {}
        for key, label in [("scanned", "📷 Scanned"), ("matched", "✨ Matches"), 
                           ("skipped", "⏭ Skipped"), ("cost", "💰 Cost")]:
            stat_widget = QVBoxLayout()
            value_label = QLabel("0" if key != "cost" else "$0.00")
            value_label.setStyleSheet("font-size: 20px; font-weight: 700; color: #818cf8;")
            stat_widget.addWidget(value_label, alignment=Qt.AlignmentFlag.AlignCenter)
            
            name_label = QLabel(label)
            name_label.setStyleSheet("font-size: 11px; color: rgba(248,250,252,0.5);")
            stat_widget.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)
            
            self.stats_labels[key] = value_label
            stats_layout.addLayout(stat_widget)
        
        progress_layout.addLayout(stats_layout)
        controls_layout.addLayout(progress_layout)
        
        main_layout.addWidget(controls_card)
        
        # Results area
        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        results_splitter.setStyleSheet("QSplitter::handle { background: rgba(255,255,255,0.1); }")
        
        # Matches panel
        matches_card = QFrame()
        matches_card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
        """)
        matches_layout = QVBoxLayout(matches_card)
        matches_layout.setContentsMargins(16, 16, 16, 16)
        
        matches_header = QHBoxLayout()
        matches_title = QLabel("🎯 Matched Photos")
        matches_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        matches_header.addWidget(matches_title)
        
        self.matches_count = QLabel("0")
        self.matches_count.setStyleSheet("""
            background: #818cf8;
            color: white;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 10px;
        """)
        matches_header.addWidget(self.matches_count)
        matches_header.addStretch()
        matches_layout.addLayout(matches_header)
        
        self.matches_scroll = QScrollArea()
        self.matches_scroll.setWidgetResizable(True)
        self.matches_container = QWidget()
        self.matches_list_layout = QVBoxLayout(self.matches_container)
        self.matches_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matches_list_layout.setSpacing(4)
        self.matches_scroll.setWidget(self.matches_container)
        matches_layout.addWidget(self.matches_scroll)
        
        results_splitter.addWidget(matches_card)
        
        # Activity panel
        activity_card = QFrame()
        activity_card.setStyleSheet("""
            QFrame {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 16px;
            }
        """)
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.setContentsMargins(16, 16, 16, 16)
        
        activity_header = QHBoxLayout()
        activity_title = QLabel("📜 Activity Feed")
        activity_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        activity_header.addWidget(activity_title)
        
        self.activity_count = QLabel("0")
        self.activity_count.setStyleSheet("""
            background: #818cf8;
            color: white;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 10px;
        """)
        activity_header.addWidget(self.activity_count)
        activity_header.addStretch()
        activity_layout.addLayout(activity_header)
        
        self.activity_scroll = QScrollArea()
        self.activity_scroll.setWidgetResizable(True)
        self.activity_container = QWidget()
        self.activity_list_layout = QVBoxLayout(self.activity_container)
        self.activity_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.activity_list_layout.setSpacing(4)
        self.activity_scroll.setWidget(self.activity_container)
        activity_layout.addWidget(self.activity_scroll)
        
        results_splitter.addWidget(activity_card)
        results_splitter.setSizes([500, 500])
        
        main_layout.addWidget(results_splitter, 1)
        
        # Footer
        footer = QHBoxLayout()
        
        self.open_album_btn = QPushButton("📂 Open Album in Photos")
        self.open_album_btn.clicked.connect(self.open_album)
        footer.addWidget(self.open_album_btn)
        
        footer.addStretch()
        
        version_label = QLabel("v1.0 • Built with PyQt6")
        version_label.setStyleSheet("color: rgba(248,250,252,0.3); font-size: 11px;")
        footer.addWidget(version_label)
        
        main_layout.addLayout(footer)
    
    def start_scan(self):
        description = self.desc_input.text().strip()
        if not description:
            QMessageBox.warning(self, "Error", "Please enter a description.")
            return
        
        # Clear previous results
        self.matches = []
        self.recent_scans = []
        self.clear_layout(self.matches_list_layout)
        self.clear_layout(self.activity_list_layout)
        self.matches_count.setText("0")
        self.activity_count.setText("0")
        
        # Update UI
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        
        # Start scanner
        limit = self.limit_input.value() or None
        dry_run = self.dry_run_cb.isChecked()
        
        self.scanner = ScannerThread(description, limit, dry_run)
        self.scanner.progress.connect(self.on_progress)
        self.scanner.match_found.connect(self.on_match)
        self.scanner.scan_item.connect(self.on_scan_item)
        self.scanner.finished_scan.connect(self.on_finished)
        self.scanner.status.connect(self.on_status)
        self.scanner.error.connect(self.on_error)
        self.scanner.start()
    
    def stop_scan(self):
        if self.scanner:
            self.scanner.stop()
        self.status_label.setText("Stopping...")
    
    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    
    def on_progress(self, current, total, filename):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"Scanning: {filename[:50]}...")
    
    def on_match(self, data):
        self.matches.append(data)
        self.matches_count.setText(str(len(self.matches)))
        
        item = PhotoItem(data, self.thumb_cache, is_match=True)
        self.matches_list_layout.insertWidget(0, item)
        
        # Keep only last 100 in UI
        if self.matches_list_layout.count() > 100:
            widget = self.matches_list_layout.takeAt(self.matches_list_layout.count() - 1)
            if widget.widget():
                widget.widget().deleteLater()
    
    def on_scan_item(self, data):
        self.recent_scans.insert(0, data)
        self.activity_count.setText(str(len([s for s in self.recent_scans if not s.get("skipped")])))
        
        # Update stats
        if self.scanner:
            self.stats_labels["scanned"].setText(str(self.scanner.stats["scanned"]))
            self.stats_labels["matched"].setText(str(self.scanner.stats["matched"]))
            self.stats_labels["skipped"].setText(str(self.scanner.stats["skipped"]))
            self.stats_labels["cost"].setText(f"${self.scanner.stats['cost']:.4f}")
        
        item = PhotoItem(data, self.thumb_cache, is_match=data.get("is_match", False))
        self.activity_list_layout.insertWidget(0, item)
        
        # Keep only last 50 in UI
        if self.activity_list_layout.count() > 50:
            widget = self.activity_list_layout.takeAt(self.activity_list_layout.count() - 1)
            if widget.widget():
                widget.widget().deleteLater()
    
    def on_status(self, message):
        self.status_label.setText(message)
    
    def on_error(self, message):
        QMessageBox.critical(self, "Error", message)
        self.on_finished({})
    
    def on_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        matched = stats.get("matched", 0)
        scanned = stats.get("scanned", 0)
        
        self.status_label.setText(f"✅ Complete! Found {matched} matches in {scanned} photos")
        self.progress_bar.setValue(self.progress_bar.maximum())
        
        if matched > 0 and not self.dry_run_cb.isChecked():
            reply = QMessageBox.question(
                self, "Add to Album?",
                f"Found {matched} matches. Add them to album '{ALBUM_NAME}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.add_matches_to_album()
    
    def add_matches_to_album(self):
        self.status_label.setText("Adding to album...")
        
        # Create album and add photos
        for match in self.matches:
            uuid = match.get("uuid")
            if uuid:
                script = f'''
                tell application "Photos"
                    try
                        set targetAlbum to album "{ALBUM_NAME}"
                    on error
                        set targetAlbum to make new album named "{ALBUM_NAME}"
                    end try
                    try
                        add {{media item id "{uuid}"}} to targetAlbum
                    end try
                end tell
                '''
                subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        
        self.status_label.setText(f"✅ Added {len(self.matches)} photos to album")
        self.open_album()
    
    def open_album(self):
        subprocess.run(["open", "-a", "Photos"], capture_output=True)


# ============================================================
# Main
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(12, 12, 12))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(248, 250, 252))
    palette.setColor(QPalette.ColorRole.Base, QColor(26, 26, 46))
    palette.setColor(QPalette.ColorRole.Text, QColor(248, 250, 252))
    palette.setColor(QPalette.ColorRole.Button, QColor(26, 26, 46))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(248, 250, 252))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(129, 140, 248))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

