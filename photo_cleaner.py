#!/usr/bin/env python3
"""
Apple Photos Cleaner - AI-powered photo organization
Scans your Photos library and identifies photos matching a description.
Matches are added to an album for easy review and deletion.
"""

import os
import sys
import json
import time
import base64
import tempfile
import argparse
import threading
import subprocess
import webbrowser
import logging
from pathlib import Path
from io import BytesIO
from typing import Optional
from datetime import datetime

# Setup logging
LOG_DIR = os.path.expanduser("~/Documents/logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "photo_cleaner.log")

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

def log(msg):
    """Log a message to file and console."""
    logger.info(msg)

# Load API key from JSON file
ENV_PATH = "/Users/mike/Documents/Keys/.env"
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for k, v in json.load(f).items():
            os.environ.setdefault(k, v)

# Optional imports
try:
    import osxphotos
    OSXPHOTOS_AVAILABLE = True
except ImportError:
    OSXPHOTOS_AVAILABLE = False
    print("❌ osxphotos not installed. Run: pip install osxphotos")
    sys.exit(1)

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from flask import Flask, jsonify, render_template_string
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
    # Register HEIC support
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        HEIC_AVAILABLE = True
    except ImportError:
        HEIC_AVAILABLE = False
except ImportError:
    PIL_AVAILABLE = False
    HEIC_AVAILABLE = False

# ============================================================
# Configuration
# ============================================================
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_DESCRIPTION = "banking, payments, and messaging screenshots (Instagram, WhatsApp, iMessage)"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7
# Only skip videos and RAW files (HEIC is handled by PIL)
SKIP_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".raw", ".cr2", ".nef", ".arw", ".dng", ".3gp", ".mkv", ".webm"}

# Cost per million tokens (approximate)
COST_PER_MILLION = {"gpt-4o": 2.50, "gpt-4o-mini": 0.15}
TOKENS_PER_IMAGE = 1100

# ============================================================
# Global State
# ============================================================
state = {
    "status": "idle",
    "scanned": 0,
    "matched": 0,
    "deleted": 0,
    "skipped": 0,
    "total": 0,
    "cost": 0.0,
    "errors": 0,
    "current_photo": None,
    "history": [],
    "matches": [],
    "description": "",
    "model": DEFAULT_MODEL,
    "is_running": False,
    "stop_requested": False,
}
state_lock = threading.Lock()

# ============================================================
# HTML Dashboard
# ============================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>🧹 Apple Photos Cleaner</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --text: #f8fafc;
      --text-secondary: #94a3b8;
      --accent: #3b82f6;
      --success: #10b981;
      --warning: #f97316;
      --danger: #ef4444;
      --photo-bg: #0f172a;
      --log-bg: #0f172a;
      --log-border: #1e293b;
    }
    [data-theme="light"] {
      --bg: #f1f5f9;
      --card: #ffffff;
      --text: #1e293b;
      --text-secondary: #64748b;
      --photo-bg: #e2e8f0;
      --log-bg: #f8fafc;
      --log-border: #e2e8f0;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 24px;
      transition: background 0.3s, color 0.3s;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    .header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
    h1 { font-size: 28px; }
    .subtitle { color: var(--text-secondary); margin-bottom: 24px; font-size: 14px; }
    
    .theme-toggle {
      background: var(--card);
      border: none;
      padding: 8px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 20px;
      transition: transform 0.2s;
    }
    .theme-toggle:hover { transform: scale(1.1); }
    
    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat {
      background: var(--card);
      padding: 16px;
      border-radius: 12px;
      text-align: center;
      transition: background 0.3s;
    }
    .stat-label { color: var(--text-secondary); font-size: 12px; text-transform: uppercase; }
    .stat-value { font-size: 28px; font-weight: 700; margin-top: 4px; }
    .stat-value.matched { color: var(--warning); }
    .stat-value.deleted { color: var(--danger); }
    .stat-value.cost { color: var(--success); }
    
    .main { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    @media (max-width: 800px) { .main { grid-template-columns: 1fr; } }
    
    .card {
      background: var(--card);
      border-radius: 12px;
      padding: 20px;
      transition: background 0.3s;
    }
    .card-title {
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 16px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .photo-area {
      background: var(--photo-bg);
      border-radius: 8px;
      min-height: 300px;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: hidden;
      transition: background 0.3s;
    }
    .photo-area img {
      max-width: 100%;
      max-height: 400px;
      object-fit: contain;
    }
    .photo-placeholder {
      color: var(--text-secondary);
      text-align: center;
      padding: 20px;
    }
    .photo-name {
      margin-top: 12px;
      font-weight: 500;
    }
    .photo-reason {
      margin-top: 8px;
      color: var(--text-secondary);
      font-size: 14px;
      line-height: 1.4;
    }
    .match-badge {
      position: absolute;
      top: 12px;
      right: 12px;
      background: var(--warning);
      color: white;
      padding: 6px 12px;
      border-radius: 20px;
      font-weight: 600;
      font-size: 14px;
    }
    
    .log {
      height: 350px;
      overflow-y: auto;
      font-family: monospace;
      font-size: 13px;
      background: var(--log-bg);
      border-radius: 8px;
      padding: 12px;
      transition: background 0.3s;
    }
    .log-entry { padding: 4px 0; border-bottom: 1px solid var(--log-border); }
    .log-match { color: var(--warning); font-weight: 600; }
    .log-added { color: var(--success); }
    
    .btn {
      padding: 10px 20px;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      font-size: 14px;
    }
    .btn-danger { background: var(--danger); color: white; }
    .btn-success { background: var(--success); color: white; }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    
    .status-badge {
      display: inline-block;
      padding: 6px 16px;
      border-radius: 20px;
      font-size: 14px;
      font-weight: 500;
    }
    .status-running { background: var(--accent); }
    .status-loading { background: var(--warning); }
    .status-complete { background: var(--success); }
    .status-idle { background: var(--text-secondary); }
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>🧹 Apple Photos Cleaner</h1>
      <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()">🌙</button>
    </div>
    <div class="subtitle" id="subtitle">Initializing...</div>
    
    <div class="stats">
      <div class="stat">
        <div class="stat-label">Status</div>
        <div class="stat-value" id="status-badge">
          <span class="status-badge status-idle" id="status">Idle</span>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">Scanned</div>
        <div class="stat-value" id="scanned">0</div>
      </div>
      <div class="stat">
        <div class="stat-label">Matches</div>
        <div class="stat-value matched" id="matched">0</div>
      </div>
      <div class="stat">
        <div class="stat-label">In Album</div>
        <div class="stat-value deleted" id="deleted">0</div>
      </div>
      <div class="stat">
        <div class="stat-label">Cost</div>
        <div class="stat-value cost" id="cost">$0.00</div>
      </div>
      <div class="stat">
        <div class="stat-label">Skipped</div>
        <div class="stat-value" id="skipped">0</div>
      </div>
      <div class="stat">
        <div class="stat-label">Errors</div>
        <div class="stat-value" id="errors">0</div>
      </div>
    </div>
    
    <div class="main">
      <div class="card">
        <div class="card-title">Current Photo</div>
        <div class="photo-area">
          <img id="photo" style="display:none;">
          <div class="photo-placeholder" id="placeholder">Waiting for scan to start...</div>
          <div class="match-badge" id="badge" style="display:none;">⚡ MATCH</div>
        </div>
        <div class="photo-name" id="photo-name"></div>
        <div class="photo-reason" id="photo-reason"></div>
      </div>
      
      <div class="card">
        <div class="card-title">
          Activity Log
          <button class="btn btn-danger" id="stop-btn" onclick="stopScan()" disabled>Stop Scan</button>
        </div>
        <div class="log" id="log"></div>
        <div style="margin-top: 12px;">
          <button class="btn btn-success" id="delete-btn" onclick="openAlbum()" disabled>
            Open Album in Photos
          </button>
        </div>
      </div>
    </div>
  </div>
  
  <script>
    function updateUI(d) {
      // Status
      const statusEl = document.getElementById('status');
      statusEl.textContent = d.status || 'Idle';
      statusEl.className = 'status-badge ' + (
        d.status?.includes('Loading') ? 'status-loading' :
        d.status?.includes('Scanning') ? 'status-running' :
        d.status?.includes('Complete') ? 'status-complete' : 'status-idle'
      );
      
      // Stats
      document.getElementById('scanned').textContent = d.scanned + (d.total ? '/' + d.total : '');
      document.getElementById('matched').textContent = d.matched || 0;
      document.getElementById('deleted').textContent = d.deleted || 0;
      document.getElementById('cost').textContent = '$' + (d.cost || 0).toFixed(4);
      document.getElementById('skipped').textContent = d.skipped || 0;
      document.getElementById('errors').textContent = d.errors || 0;
      
      // Subtitle
      const sub = d.description ? `"${d.description}" | ${d.model}` : 'Initializing...';
      document.getElementById('subtitle').textContent = sub;
      
      // Buttons
      document.getElementById('stop-btn').disabled = !d.is_running;
      document.getElementById('delete-btn').disabled = d.deleted === 0;
      
      // Log
      const logEl = document.getElementById('log');
      logEl.innerHTML = (d.history || []).map(h => {
        const cls = h.includes('MATCH') ? 'log-match' : h.includes('Album') ? 'log-added' : '';
        return `<div class="log-entry ${cls}">${h}</div>`;
      }).join('');
      logEl.scrollTop = logEl.scrollHeight;
      
      // Photo
      const photo = d.current_photo;
      const img = document.getElementById('photo');
      const placeholder = document.getElementById('placeholder');
      const badge = document.getElementById('badge');
      
      if (photo && photo.data) {
        img.src = 'data:image/jpeg;base64,' + photo.data;
        img.style.display = 'block';
        placeholder.style.display = 'none';
        document.getElementById('photo-name').textContent = photo.name || '';
        document.getElementById('photo-reason').textContent = photo.reason || '';
        badge.style.display = photo.is_match ? 'block' : 'none';
      } else {
        img.style.display = 'none';
        placeholder.style.display = 'block';
        placeholder.textContent = d.status?.includes('Loading') 
          ? 'Loading Photos library... (may take a few minutes)'
          : 'Waiting for photos...';
        badge.style.display = 'none';
      }
    }
    
    function fetchStats() {
      fetch('/stats')
        .then(r => r.json())
        .then(updateUI)
        .catch(() => {});
    }
    
    function stopScan() {
      fetch('/stop', { method: 'POST' }).then(() => fetchStats());
    }
    
    function openAlbum() {
      fetch('/open-album', { method: 'POST' });
    }
    
    setInterval(fetchStats, 500);
    fetchStats();
    
    // Theme handling
    function getSystemTheme() {
      return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    
    function setTheme(theme) {
      document.documentElement.setAttribute('data-theme', theme);
      document.getElementById('theme-toggle').textContent = theme === 'light' ? '🌙' : '☀️';
      localStorage.setItem('theme', theme);
    }
    
    function toggleTheme() {
      const current = document.documentElement.getAttribute('data-theme') || getSystemTheme();
      setTheme(current === 'light' ? 'dark' : 'light');
    }
    
    // Initialize theme from localStorage or system preference
    (function() {
      const saved = localStorage.getItem('theme');
      setTheme(saved || getSystemTheme());
      
      // Listen for system theme changes
      window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
        if (!localStorage.getItem('theme')) {
          setTheme(e.matches ? 'light' : 'dark');
        }
      });
    })();
  </script>
</body>
</html>
"""

# ============================================================
# Photo Cleaner Class
# ============================================================
class PhotoCleaner:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.client = None
        self.db = None
        
        if not os.getenv("OPENAI_API_KEY"):
            print("❌ OPENAI_API_KEY not set")
            print(f"   Add it to: {ENV_PATH}")
            sys.exit(1)
        
        self.client = openai.OpenAI()
    
    def load_library(self, recent_days: Optional[int] = None) -> int:
        """Load the Photos library."""
        with state_lock:
            state["status"] = "Loading Photos library..."
        
        log("Loading Photos library...")
        print("📚 Loading Photos library...")
        start = time.time()
        self.db = osxphotos.PhotosDB()
        elapsed = time.time() - start
        
        # Get photos with optional date filter
        if recent_days:
            from datetime import datetime, timedelta
            cutoff = datetime.now() - timedelta(days=recent_days)
            photos = [p for p in self.db.photos() if p.date and p.date >= cutoff]
            log(f"Loaded {len(photos):,} photos from last {recent_days} days in {elapsed:.1f}s")
            print(f"   Loaded {len(photos):,} photos (last {recent_days} days) in {elapsed:.1f}s")
        else:
            photos = list(self.db.photos())
            log(f"Loaded {len(photos):,} photos in {elapsed:.1f}s")
            print(f"   Loaded {len(photos):,} photos in {elapsed:.1f}s")
        
        return photos
    
    def get_photos(self, limit: Optional[int] = None, recent_days: Optional[int] = None) -> list:
        """Get photos from library."""
        if not self.db:
            photos = self.load_library(recent_days)
        else:
            if recent_days:
                from datetime import datetime, timedelta
                cutoff = datetime.now() - timedelta(days=recent_days)
                photos = [p for p in self.db.photos() if p.date and p.date >= cutoff]
            else:
                photos = list(self.db.photos())
        
        if limit:
            photos = photos[:limit]
        return photos
    
    def encode_photo(self, photo) -> tuple[Optional[str], str]:
        """Encode photo to base64 for API. Returns (data, media_type) or (None, skip_reason)."""
        try:
            # Try direct path first (fastest)
            filepath = Path(photo.path) if photo.path else None
            
            if not filepath or not filepath.exists():
                # Fallback: export
                with tempfile.TemporaryDirectory() as tmpdir:
                    exported = photo.export(tmpdir, use_photos_export=False)
                    if not exported:
                        return None, "export_failed"
                    filepath = Path(exported[0])
            
            ext = filepath.suffix.lower()
            if ext in SKIP_EXTENSIONS:
                return None, f"skip_{ext}"
            
            # Resize for faster API
            if PIL_AVAILABLE:
                try:
                    with Image.open(filepath) as img:
                        if img.mode in ('RGBA', 'P'):
                            img = img.convert('RGB')
                        img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
                        buffer = BytesIO()
                        img.save(buffer, format='JPEG', quality=80)
                        buffer.seek(0)
                        return base64.b64encode(buffer.read()).decode(), "image/jpeg"
                except Exception as pil_err:
                    # HEIC without pillow-heif, or other PIL error
                    if ext == ".heic" and not HEIC_AVAILABLE:
                        return None, "skip_heic_no_support"
                    return None, f"pil_error"
            
            # Fallback: read original (only works for jpg/png)
            if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                with open(filepath, "rb") as f:
                    return base64.b64encode(f.read()).decode(), "image/jpeg"
            
            return None, f"skip_{ext}"
                
        except Exception as e:
            return None, "error"
    
    def analyze_photo(self, image_data: str, description: str) -> dict:
        """Analyze photo with OpenAI Vision API."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": 'Analyze if image matches description. Reply ONLY with JSON: {"match": true/false, "confidence": 0.0-1.0, "reason": "brief reason"}'
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Does this match: '{description}'?"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}", "detail": "low"}}
                        ]
                    }
                ],
                max_tokens=150,
                timeout=30
            )
            
            content = response.choices[0].message.content or ""
            
            # Parse JSON from response
            try:
                # Handle markdown code blocks
                if "```" in content:
                    content = content.split("```")[1]
                    if content.startswith("json"):
                        content = content[4:]
                return json.loads(content.strip())
            except:
                return {"match": False, "confidence": 0, "reason": "Could not parse response"}
                
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": f"API error: {str(e)[:50]}"}
    
    def add_to_album(self, uuids: list) -> int:
        """Add photos to deletion album via AppleScript."""
        if not uuids:
            return 0
        
        album_name = "🤖 AI Matches - To Delete"
        uuid_list = '", "'.join(uuids)
        
        script = f'''
        tell application "Photos"
            set albumName to "{album_name}"
            try
                set targetAlbum to album albumName
            on error
                set targetAlbum to make new album named albumName
            end try
            
            set addedCount to 0
            repeat with photoUUID in {{"{uuid_list}"}}
                try
                    add {{media item id photoUUID}} to targetAlbum
                    set addedCount to addedCount + 1
                end try
            end repeat
            return addedCount
        end tell
        '''
        
        try:
            result = subprocess.run(["osascript", "-e", script], 
                                   capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip())
            elif result.stderr:
                log(f"ALBUM ERROR | {result.stderr[:100]}")
        except subprocess.TimeoutExpired:
            log("ALBUM TIMEOUT")
        except Exception as e:
            log(f"ALBUM EXCEPTION | {e}")
        return 0
    
    def open_album(self):
        """Open Photos app to the deletion album."""
        script = '''
        tell application "Photos"
            activate
            delay 0.5
            try
                reveal album "🤖 AI Matches - To Delete"
            end try
        end tell
        '''
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    
    def scan(self, description: str, limit: Optional[int] = None, 
             dry_run: bool = False, realtime: bool = True, recent_days: Optional[int] = None):
        """Scan photos and find matches."""
        global state
        
        # Initialize state
        with state_lock:
            state.update({
                "status": "Loading Photos library...",
                "scanned": 0,
                "matched": 0,
                "deleted": 0,
                "skipped": 0,
                "total": 0,
                "cost": 0.0,
                "errors": 0,
                "current_photo": None,
                "history": [],
                "matches": [],
                "description": description,
                "model": self.model,
                "is_running": True,
                "stop_requested": False,
            })
        
        # Load photos
        photos = self.get_photos(limit, recent_days)
        total = len(photos)
        
        with state_lock:
            state["total"] = total
            state["status"] = f"Scanning (0/{total})"
            state["history"].append(f"📚 Found {total:,} photos to scan")
        
        log(f"START | desc='{description}' | model={self.model} | total={total} | dry_run={dry_run}")
        print(f"\n🔍 Scanning {total:,} photos for: \"{description}\"")
        print(f"   Model: {self.model} | Dry run: {dry_run}\n")
        
        cost_per_image = COST_PER_MILLION.get(self.model, 0.15) * TOKENS_PER_IMAGE / 1_000_000
        
        for i, photo in enumerate(photos):
            # Check stop
            if state["stop_requested"]:
                print("\n⏹️  Stopped by user")
                break
            
            filename = photo.original_filename or f"photo_{i}"
            
            # Encode
            img_data, result_type = self.encode_photo(photo)
            
            if not img_data:
                with state_lock:
                    if result_type.startswith("skip_"):
                        state["skipped"] += 1
                    else:
                        state["errors"] += 1
                    state["scanned"] += 1
                    state["status"] = f"Scanning ({i+1}/{total})"
                    state["history"].append(f"   ⏭️ {filename} ({result_type})")
                continue
            
            # Analyze
            result = self.analyze_photo(img_data, description)
            
            with state_lock:
                state["scanned"] += 1
                state["cost"] += cost_per_image
                state["status"] = f"Scanning ({i+1}/{total})"
                
                is_match = result.get("match", False) and result.get("confidence", 0) >= CONFIDENCE_THRESHOLD
                
                state["current_photo"] = {
                    "name": filename,
                    "data": img_data[:50000],  # Limit size for UI
                    "reason": result.get("reason", ""),
                    "confidence": result.get("confidence", 0),
                    "is_match": is_match,
                }
                
                if is_match:
                    state["matched"] += 1
                    state["matches"].append({"uuid": photo.uuid, "filename": filename})
                    state["history"].append(f"⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                    
                    log(f"MATCH | {filename} | conf={result.get('confidence', 0):.2f}")
                    print(f"   ⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                    print(f"      {result.get('reason', '')}")
                    
                    # Add to album in realtime
                    if realtime and not dry_run:
                        added = self.add_to_album([photo.uuid])
                        if added:
                            state["deleted"] += 1
                            state["history"].append(f"   📁 Added to album")
                            log(f"ADDED TO ALBUM | {filename}")
                            print(f"      📁 Added to album")
                        else:
                            log(f"ALBUM FAILED | {filename}")
                else:
                    state["history"].append(f"   {filename}")
                
                # Keep history manageable
                if len(state["history"]) > 200:
                    state["history"] = state["history"][-100:]
        
        # Summary
        with state_lock:
            state["status"] = "Complete" if not state["stop_requested"] else "Stopped"
            state["is_running"] = False
        
        print(f"\n{'='*50}")
        print(f"📊 Summary:")
        print(f"   Scanned: {state['scanned']}")
        print(f"   Matches: {state['matched']}")
        print(f"   In Album: {state['deleted']}")
        print(f"   Skipped: {state['skipped']} (videos/RAW)")
        print(f"   Errors: {state['errors']}")
        print(f"   Cost: ${state['cost']:.4f}")
        
        if state["deleted"] > 0:
            print(f"\n📁 Opening Photos app...")
            print(f"   Select all (⌘A) → Delete (⌫)")
            self.open_album()
        
        print(f"{'='*50}\n")


# ============================================================
# Flask Dashboard
# ============================================================
def create_app(cleaner: PhotoCleaner, description: str, limit: Optional[int], 
               dry_run: bool, realtime: bool, recent_days: Optional[int] = None):
    """Create Flask app with dashboard."""
    app = Flask(__name__)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    
    @app.route('/')
    def index():
        return render_template_string(HTML_TEMPLATE)
    
    @app.route('/stats')
    def stats():
        with state_lock:
            return jsonify(state.copy())
    
    @app.route('/stop', methods=['POST'])
    def stop():
        with state_lock:
            state["stop_requested"] = True
        return jsonify({"ok": True})
    
    @app.route('/open-album', methods=['POST'])
    def open_album():
        cleaner.open_album()
        return jsonify({"ok": True})
    
    # Start scan in background
    def run_scan():
        cleaner.scan(description, limit, dry_run, realtime, recent_days)
    
    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    
    return app


def find_free_port(start=5000, end=5020):
    """Find an available port."""
    import socket
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('localhost', port))
                return port
        except:
            continue
    return start


# ============================================================
# Interactive Mode
# ============================================================
def run_interactive():
    """Run in interactive mode with prompts."""
    print("\n" + "="*50)
    print("  🧹 Apple Photos Cleaner")
    print("="*50 + "\n")
    
    # Model selection
    models = [
        ("gpt-4o-mini", "Fast & reliable (default)"),
        ("gpt-4o", "Higher accuracy, slower"),
    ]
    print("1. Model:")
    for i, (m, desc) in enumerate(models, 1):
        print(f"   {i}) {m} - {desc}")
    choice = input("   Select [1]: ").strip()
    model = models[int(choice)-1][0] if choice.isdigit() and 1 <= int(choice) <= len(models) else models[0][0]
    
    # Description
    print(f"\n2. What photos to find?")
    print(f"   Default: '{DEFAULT_DESCRIPTION}'")
    desc = input("   Description [Enter for default]: ").strip() or DEFAULT_DESCRIPTION
    
    # Recent days (faster startup)
    days = input("\n3. Scan photos from last N days [90]: ").strip()
    recent_days = 90 if days == "" else (int(days) if days.isdigit() else None)
    if days.lower() == "all":
        recent_days = None
    
    # Limit
    lim = input("\n4. Additional limit (or 'all') [all]: ").strip().lower()
    limit = None if lim in ("", "all") else (int(lim) if lim.isdigit() else None)
    
    # Dashboard
    dashboard = input("\n5. Open visual dashboard? (Y/n) [Y]: ").strip().lower() != "n"
    
    # Dry run
    dry_run = input("\n6. Dry run (preview only)? (y/N) [N]: ").strip().lower() == "y"
    
    # Realtime
    realtime = True
    if not dry_run:
        realtime = input("\n7. Add matches to album immediately? (Y/n) [Y]: ").strip().lower() != "n"
    
    # Run
    cleaner = PhotoCleaner(model=model)
    
    if dashboard and FLASK_AVAILABLE:
        port = find_free_port()
        print(f"\n🚀 Dashboard: http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")
        
        app = create_app(cleaner, desc, limit, dry_run, realtime, recent_days)
        app.run(host='localhost', port=port, threaded=True)
    else:
        cleaner.scan(desc, limit, dry_run, realtime, recent_days)


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="AI-powered Apple Photos cleaner")
    parser.add_argument("description", nargs="?", help="Description of photos to find")
    parser.add_argument("--limit", type=int, help="Limit photos to scan")
    parser.add_argument("--recent", type=int, default=90, help="Only scan last N days (default: 90, use 0 for all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model")
    parser.add_argument("--dashboard", action="store_true", help="Open web dashboard")
    parser.add_argument("--no-realtime", action="store_true", help="Don't add to album in realtime")
    
    args = parser.parse_args()
    
    # Interactive mode if no description
    if not args.description:
        run_interactive()
        return
    
    # CLI mode
    recent_days = args.recent if args.recent > 0 else None
    cleaner = PhotoCleaner(model=args.model)
    
    if args.dashboard and FLASK_AVAILABLE:
        port = find_free_port()
        print(f"🚀 Dashboard: http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")
        
        app = create_app(cleaner, args.description, args.limit, 
                        args.dry_run, not args.no_realtime, recent_days)
        app.run(host='localhost', port=port, threaded=True)
    else:
        cleaner.scan(args.description, args.limit, args.dry_run, not args.no_realtime, recent_days)


if __name__ == "__main__":
    main()
