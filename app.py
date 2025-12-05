#!/usr/bin/env python3
"""
Photo Cleaner Pro - Premium Desktop App
AI-powered photo organization with elegant UI.
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

# ============================================================
# Helpers
# ============================================================
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
# PyQt6 Application
# ============================================================
try:
    from PyQt6.QtWidgets import *
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve
    from PyQt6.QtGui import QPixmap, QImage, QPalette, QColor, QFont, QLinearGradient, QPainter, QBrush
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
        self.status.emit(f"Scanning {total:,} photos")
        
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
            
            data = {
                **p,
                "is_match": is_match,
                "confidence": result.get("confidence", 0),
                "reason": result.get("reason", "")[:100]
            }
            
            if is_match:
                self.stats["matched"] += 1
                self.matches.append(data)
            
            self.photo_scanned.emit(data)
        
        self.stats["matches"] = self.matches
        self.finished_scan.emit(self.stats)


# ============================================================
# Premium UI Components
# ============================================================
class GlassCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(255,255,255,0.08),
                    stop:1 rgba(255,255,255,0.02));
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 20px;
            }
        """)


class StatWidget(QWidget):
    def __init__(self, value="0", label="", color="#818cf8"):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(4)
        
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"font-size: 32px; font-weight: 700; color: {color}; font-family: 'SF Pro Display', -apple-system;")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.value_label)
        
        desc = QLabel(label)
        desc.setStyleSheet("font-size: 12px; color: rgba(255,255,255,0.4); text-transform: uppercase; letter-spacing: 1px;")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc)
    
    def setValue(self, v):
        self.value_label.setText(str(v))


class MatchCard(QFrame):
    def __init__(self, data, small=False):
        super().__init__()
        conf = int(data.get("confidence", 0) * 100)
        
        self.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(52, 211, 153, 0.15),
                    stop:1 rgba(52, 211, 153, 0.05));
                border: 1px solid rgba(52, 211, 153, 0.3);
                border-radius: {'10' if small else '16'}px;
            }}
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        
        # Thumbnail
        thumb = QLabel()
        size = 48 if small else 64
        thumb.setFixedSize(size, size)
        thumb.setStyleSheet(f"border-radius: {size//4}px; background: rgba(0,0,0,0.3);")
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((size, size))
                buf = BytesIO()
                img.save(buf, format='JPEG')
                qimg = QImage.fromData(buf.getvalue())
                thumb.setPixmap(QPixmap.fromImage(qimg).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        except:
            thumb.setText("📷")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(thumb)
        
        # Info
        info = QVBoxLayout()
        info.setSpacing(2)
        
        name = QLabel(data.get('filename', '')[:30])
        name.setStyleSheet(f"font-size: {'12' if small else '14'}px; font-weight: 600; color: #f0fdf4;")
        info.addWidget(name)
        
        reason = QLabel(data.get('reason', '')[:50])
        reason.setStyleSheet(f"font-size: {'10' if small else '11'}px; color: rgba(255,255,255,0.5);")
        info.addWidget(reason)
        
        layout.addLayout(info, 1)
        
        # Badge
        badge = QLabel(f"{conf}%")
        badge.setStyleSheet(f"""
            background: #34d399;
            color: #022c22;
            font-size: {'11' if small else '13'}px;
            font-weight: 700;
            padding: {'4px 8px' if small else '6px 12px'};
            border-radius: {'8' if small else '10'}px;
        """)
        layout.addWidget(badge)


# ============================================================
# Main Window
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scanner = None
        self.matches = []
        self.current_photo = None
        self.setup_ui()
    
    def setup_ui(self):
        self.setWindowTitle("Photo Cleaner Pro")
        self.setMinimumSize(1100, 750)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0a0a0f,
                    stop:0.5 #12121a,
                    stop:1 #0d1117);
            }
            QLabel { color: #e2e8f0; }
            QLineEdit {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
                padding: 14px 18px;
                color: #f1f5f9;
                font-size: 15px;
                selection-background-color: #6366f1;
            }
            QLineEdit:focus {
                border-color: rgba(129, 140, 248, 0.5);
                background: rgba(255,255,255,0.05);
            }
            QSpinBox {
                background: rgba(255,255,255,0.03);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 10px;
                padding: 10px;
                color: #f1f5f9;
            }
            QCheckBox { color: rgba(255,255,255,0.6); spacing: 8px; }
            QCheckBox::indicator {
                width: 18px; height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(255,255,255,0.2);
                background: rgba(255,255,255,0.05);
            }
            QCheckBox::indicator:checked {
                background: #818cf8;
                border-color: #818cf8;
            }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,0.15);
                border-radius: 4px;
                min-height: 40px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # ==================== LEFT PANEL (Main Content) ====================
        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(32, 28, 20, 28)
        left_layout.setSpacing(24)
        
        # Header
        header = QHBoxLayout()
        
        title_area = QVBoxLayout()
        title = QLabel("Photo Cleaner")
        title.setStyleSheet("font-size: 28px; font-weight: 700; color: #f8fafc; font-family: 'SF Pro Display', -apple-system;")
        title_area.addWidget(title)
        
        subtitle = QLabel("AI-powered photo organization")
        subtitle.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.4); margin-top: -4px;")
        title_area.addWidget(subtitle)
        header.addLayout(title_area)
        
        header.addStretch()
        
        # API Status
        api_key = load_api_key()
        status_dot = "●" if api_key else "○"
        status_color = "#34d399" if api_key else "#f87171"
        api_label = QLabel(f"{status_dot} {'Connected' if api_key else 'No API Key'}")
        api_label.setStyleSheet(f"color: {status_color}; font-size: 12px; font-weight: 500;")
        header.addWidget(api_label)
        
        left_layout.addLayout(header)
        
        # Search Input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Describe photos to find... (e.g., screenshots of banking apps, receipts)")
        self.search_input.setText("banking, payments, and messaging screenshots")
        left_layout.addWidget(self.search_input)
        
        # Controls Row
        controls = QHBoxLayout()
        controls.setSpacing(16)
        
        limit_layout = QHBoxLayout()
        limit_layout.setSpacing(8)
        limit_label = QLabel("Limit")
        limit_label.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 13px;")
        limit_layout.addWidget(limit_label)
        
        self.limit_input = QSpinBox()
        self.limit_input.setRange(0, 200000)
        self.limit_input.setValue(100)
        self.limit_input.setSpecialValueText("All")
        self.limit_input.setFixedWidth(90)
        limit_layout.addWidget(self.limit_input)
        controls.addLayout(limit_layout)
        
        self.dry_run = QCheckBox("Preview only")
        controls.addWidget(self.dry_run)
        
        controls.addStretch()
        
        self.start_btn = QPushButton("Start Scan")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #8b5cf6);
                border: none;
                border-radius: 12px;
                padding: 14px 32px;
                color: white;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4f46e5, stop:1 #7c3aed);
            }
            QPushButton:disabled { background: rgba(255,255,255,0.1); color: rgba(255,255,255,0.3); }
        """)
        self.start_btn.clicked.connect(self.start_scan)
        controls.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background: rgba(248, 113, 113, 0.1);
                border: 1px solid rgba(248, 113, 113, 0.3);
                border-radius: 12px;
                padding: 14px 24px;
                color: #f87171;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: rgba(248, 113, 113, 0.2); }
        """)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_scan)
        controls.addWidget(self.stop_btn)
        
        left_layout.addLayout(controls)
        
        # ==================== LARGE PHOTO PREVIEW ====================
        preview_card = GlassCard()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(24, 24, 24, 24)
        preview_layout.setSpacing(16)
        
        # Preview header
        preview_header = QHBoxLayout()
        preview_title = QLabel("Currently Scanning")
        preview_title.setStyleSheet("font-size: 14px; font-weight: 600; color: rgba(255,255,255,0.6);")
        preview_header.addWidget(preview_title)
        preview_header.addStretch()
        
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.4);")
        preview_header.addWidget(self.progress_label)
        preview_layout.addLayout(preview_header)
        
        # Large image
        self.photo_preview = QLabel()
        self.photo_preview.setFixedSize(400, 400)
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setStyleSheet("""
            background: rgba(0,0,0,0.3);
            border-radius: 16px;
            border: 1px solid rgba(255,255,255,0.05);
        """)
        self.photo_preview.setText("📷")
        self.photo_preview.setStyleSheet(self.photo_preview.styleSheet() + "font-size: 64px; color: rgba(255,255,255,0.1);")
        preview_layout.addWidget(self.photo_preview, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Photo info
        self.photo_name = QLabel("")
        self.photo_name.setStyleSheet("font-size: 15px; font-weight: 600; color: #f1f5f9;")
        self.photo_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.photo_name)
        
        self.photo_reason = QLabel("")
        self.photo_reason.setStyleSheet("font-size: 13px; color: rgba(255,255,255,0.5);")
        self.photo_reason.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_reason.setWordWrap(True)
        preview_layout.addWidget(self.photo_reason)
        
        # Match indicator
        self.match_indicator = QLabel("")
        self.match_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.match_indicator.hide()
        preview_layout.addWidget(self.match_indicator)
        
        left_layout.addWidget(preview_card, 1)
        
        # Stats Row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)
        
        self.stat_scanned = StatWidget("0", "Scanned", "#818cf8")
        self.stat_matches = StatWidget("0", "Matches", "#34d399")
        self.stat_cost = StatWidget("$0.00", "Cost", "#fbbf24")
        
        for stat in [self.stat_scanned, self.stat_matches, self.stat_cost]:
            card = GlassCard()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(0, 0, 0, 0)
            card_layout.addWidget(stat)
            stats_layout.addWidget(card)
        
        left_layout.addLayout(stats_layout)
        
        layout.addWidget(left, 2)
        
        # ==================== RIGHT PANEL (Matches) ====================
        right = QFrame()
        right.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.02);
                border-left: 1px solid rgba(255,255,255,0.05);
            }
        """)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(20, 28, 20, 28)
        right_layout.setSpacing(16)
        
        # Matches header
        matches_header = QHBoxLayout()
        matches_title = QLabel("Matches")
        matches_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f8fafc;")
        matches_header.addWidget(matches_title)
        
        self.matches_badge = QLabel("0")
        self.matches_badge.setStyleSheet("""
            background: #34d399;
            color: #022c22;
            font-size: 12px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 10px;
        """)
        matches_header.addWidget(self.matches_badge)
        matches_header.addStretch()
        right_layout.addLayout(matches_header)
        
        # Matches list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.matches_container = QWidget()
        self.matches_list = QVBoxLayout(self.matches_container)
        self.matches_list.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.matches_list.setSpacing(8)
        scroll.setWidget(self.matches_container)
        right_layout.addWidget(scroll, 1)
        
        # Action buttons
        self.open_btn = QPushButton("Open in Photos")
        self.open_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 10px;
                padding: 12px;
                color: #e2e8f0;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover { background: rgba(255,255,255,0.1); }
        """)
        self.open_btn.clicked.connect(lambda: subprocess.run(["open", "-a", "Photos"]))
        right_layout.addWidget(self.open_btn)
        
        layout.addWidget(right, 1)
    
    def start_scan(self):
        desc = self.search_input.text().strip()
        if not desc:
            QMessageBox.warning(self, "Error", "Enter a description")
            return
        
        self.matches = []
        self.clear_layout(self.matches_list)
        self.matches_badge.setText("0")
        self.stat_scanned.setValue("0")
        self.stat_matches.setValue("0")
        self.stat_cost.setValue("$0.00")
        
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        limit = self.limit_input.value() or None
        self.scanner = ScannerThread(desc, limit, self.dry_run.isChecked())
        self.scanner.progress.connect(self.on_progress)
        self.scanner.photo_scanned.connect(self.on_photo)
        self.scanner.finished_scan.connect(self.on_finished)
        self.scanner.status.connect(lambda s: self.progress_label.setText(s))
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
    
    def on_progress(self, current, total):
        pct = int(current / total * 100) if total else 0
        self.progress_label.setText(f"{current:,} / {total:,}  ({pct}%)")
    
    def on_photo(self, data):
        # Update stats
        self.stat_scanned.setValue(f"{self.scanner.stats['scanned']:,}")
        self.stat_matches.setValue(f"{self.scanner.stats['matched']:,}")
        self.stat_cost.setValue(f"${self.scanner.stats['cost']:.4f}")
        
        # Update large preview
        try:
            with Image.open(data['path']) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.thumbnail((400, 400), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=90)
                qimg = QImage.fromData(buf.getvalue())
                self.photo_preview.setPixmap(QPixmap.fromImage(qimg))
        except:
            pass
        
        self.photo_name.setText(data.get('filename', '')[:50])
        self.photo_reason.setText(data.get('reason', ''))
        
        # Show match indicator
        if data.get('is_match'):
            conf = int(data.get('confidence', 0) * 100)
            self.match_indicator.setText(f"✨ MATCH • {conf}% confidence")
            self.match_indicator.setStyleSheet("""
                background: rgba(52, 211, 153, 0.2);
                color: #34d399;
                font-size: 14px;
                font-weight: 600;
                padding: 10px 20px;
                border-radius: 20px;
            """)
            self.match_indicator.show()
            
            # Add to matches list
            self.matches.append(data)
            self.matches_badge.setText(str(len(self.matches)))
            self.matches_list.insertWidget(0, MatchCard(data, small=True))
        else:
            self.match_indicator.hide()
    
    def on_finished(self, stats):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_label.setText(f"✓ Complete • {stats['matched']} matches found")
        
        if self.matches and not self.dry_run.isChecked():
            QMessageBox.information(self, "Scan Complete", 
                f"Found {len(self.matches)} matches!\n\nClick 'Open in Photos' to review them.")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    # Dark palette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(10, 10, 15))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(226, 232, 240))
    palette.setColor(QPalette.ColorRole.Base, QColor(15, 15, 20))
    palette.setColor(QPalette.ColorRole.Text, QColor(241, 245, 249))
    app.setPalette(palette)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
