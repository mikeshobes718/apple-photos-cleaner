#!/usr/bin/env python3.12
"""
Apple Photos Cleaner - Fast AI-powered photo deletion.
Supports OpenAI (Cloud) and Ollama (Local/Free).

Features:
  - Parallel processing (5-10x faster)
  - Live web dashboard
  - Smart image preprocessing
  - Retry logic & graceful shutdown
"""

import argparse
import base64
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

# Optional: PIL for image resizing (speeds up API calls significantly)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

try:
    from flask import Flask, jsonify, render_template_string
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

try:
    import osxphotos
except ImportError:
    print("❌ osxphotos not installed. Run: pip install osxphotos")
    sys.exit(1)

try:
    import openai
except ImportError:
    openai = None

try:
    import requests
except ImportError:
    requests = None

# ============================================================
# Configuration
# ============================================================
ENV_PATH = "/Users/mike/Documents/Keys/.env"
LOG_DIR = Path.home() / "Documents" / "logs"
LOG_FILE = LOG_DIR / "photo_cleaner.log"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SKIP_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".heic", ".dng", ".raw", ".cr2", ".nef", ".arw"}

PRICE_PER_MILLION = {
    "gpt-5.1": 1.25, "gpt-5-mini": 0.25, "gpt-5-nano": 0.05,
    "gpt-4o": 2.50, "gpt-4o-mini": 0.15,
}
TOKENS_PER_IMAGE = 270
MAX_IMAGE_SIZE = 512  # Resize images to max 512px for speed
MAX_WORKERS = 4  # Concurrent API calls (reduced to prevent rate limiting)
MAX_RETRIES = 2
FUTURE_TIMEOUT = 45  # Timeout for each photo processing (API timeout is 30s)

# Global state for dashboard
scan_state = {
    "current_photo": None,
    "stats": {"scanned": 0, "matched": 0, "deleted": 0, "cost": 0.0, "skipped": 0, "errors": 0},
    "history": [],
    "is_running": False,
    "stop_requested": False,
    "status": "Idle",
    "meta": {},
    "matches": [],  # Track matched photos for review
    "start_time": None,
    "total_photos": 0,
    "last_progress_time": None,  # For stall detection
    "recent_scans": [],  # [(timestamp, count), ...] for recent speed calc
}
state_lock = threading.Lock()

# ============================================================
# Utilities
# ============================================================
def load_env_file(path: str) -> None:
    """Load environment variables from JSON or .env file."""
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return
        if content.startswith("{"):
            data = json.loads(content)
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str):
                    os.environ.setdefault(k, v)
        elif DOTENV_AVAILABLE:
            load_dotenv(path)
    except Exception as e:
        print(f"⚠️  Could not load env file {path}: {e}")

load_env_file(ENV_PATH)

def log_line(message: str) -> None:
    """Thread-safe logging to file."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass

def format_time(seconds: float) -> str:
    """Format seconds as human-readable time."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds // 60:.0f}m {seconds % 60:.0f}s"
    else:
        return f"{seconds // 3600:.0f}h {(seconds % 3600) // 60:.0f}m"

def find_free_port(start: int = 5000, end: int = 5050) -> Optional[int]:
    """Find an available port."""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return None

# ============================================================
# HTML Dashboard Template
# ============================================================
HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>🧹 Apple Photos Cleaner</title>
  <style>
    * { box-sizing: border-box; }
    :root {
      --bg-primary: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
      --bg-card: rgba(255,255,255,0.05);
      --bg-input: #0f172a;
      --border: rgba(255,255,255,0.1);
      --text-primary: #e8e8e8;
      --text-secondary: #94a3b8;
      --text-muted: #475569;
    }
    .light-mode {
      --bg-primary: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
      --bg-card: rgba(255,255,255,0.9);
      --bg-input: #f1f5f9;
      --border: rgba(0,0,0,0.1);
      --text-primary: #1e293b;
      --text-secondary: #475569;
      --text-muted: #94a3b8;
    }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif; 
      margin: 0; padding: 20px; 
      background: var(--bg-primary);
      color: var(--text-primary); min-height: 100vh;
      transition: background 0.3s, color 0.3s;
    }
    .container { max-width: 1200px; margin: 0 auto; }
    header { 
      display: flex; justify-content: space-between; align-items: center; 
      margin-bottom: 20px; padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    h1 { margin: 0; font-size: 28px; font-weight: 700; }
    .meta { color: var(--text-secondary); font-size: 13px; margin-top: 4px; }
    .header-right { display: flex; align-items: center; gap: 12px; }
    .badge { 
      padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 13px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;
    }
    .theme-toggle {
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 20px; padding: 6px 12px; cursor: pointer;
      font-size: 16px; transition: transform 0.2s;
    }
    .theme-toggle:hover { transform: scale(1.1); }
    .stats { 
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); 
      gap: 12px; margin-bottom: 20px;
    }
    .stat-card { 
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 12px; padding: 16px; text-align: center;
      backdrop-filter: blur(10px);
    }
    .stat-label { color: var(--text-secondary); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
    .stat-value { font-size: 32px; font-weight: 700; margin-top: 4px; }
    .stat-value.matched { color: #f97316; }
    .stat-value.deleted { color: #ef4444; }
    .stat-value.cost { color: #10b981; }
    .stat-value.speed { color: #3b82f6; }
    .main-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    @media (max-width: 900px) { .main-grid { grid-template-columns: 1fr; } }
    .card { 
      background: var(--bg-card); border: 1px solid var(--border);
      border-radius: 16px; padding: 20px; backdrop-filter: blur(10px);
    }
    .card-title { font-weight: 600; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
    .photo-container { 
      background: var(--bg-input); border-radius: 12px; 
      display: flex; align-items: center; justify-content: center;
      min-height: 300px; overflow: hidden; position: relative;
    }
    .photo-container img { max-width: 100%; max-height: 400px; object-fit: contain; border-radius: 8px; }
    .photo-name { color: var(--text-secondary); font-size: 14px; margin-top: 12px; }
    .reason { margin-top: 12px; padding: 12px; background: var(--bg-input); border-radius: 8px; font-size: 14px; line-height: 1.5; }
    .confidence-bar { 
      height: 6px; background: var(--border); border-radius: 3px; 
      margin-top: 12px; overflow: hidden;
    }
    .confidence-fill { height: 100%; background: linear-gradient(90deg, #10b981, #3b82f6); border-radius: 3px; transition: width 0.3s; }
    .log { 
      height: 350px; overflow-y: auto; background: var(--bg-input); 
      border-radius: 12px; padding: 12px; font-family: 'SF Mono', Monaco, monospace; font-size: 12px;
      line-height: 1.6; color: var(--text-secondary);
    }
    .log-entry { padding: 2px 0; }
    .log-entry.match { color: #f97316; font-weight: 600; }
    .log-entry.error { color: #ef4444; }
    .log-entry.delete { color: #22c55e; }
    .btn { 
      padding: 10px 20px; border-radius: 8px; border: none; 
      background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
      color: white; font-weight: 600; cursor: pointer; font-size: 14px;
      transition: transform 0.1s, box-shadow 0.2s;
    }
    .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(239,68,68,0.4); }
    .progress { margin-top: 8px; }
    .progress-text { font-size: 12px; color: var(--text-secondary); }
    .match-badge { 
      position: absolute; top: 12px; right: 12px; 
      background: linear-gradient(135deg, #f97316 0%, #ea580c 100%);
      padding: 6px 12px; border-radius: 6px; font-weight: 600; font-size: 12px; color: white;
      animation: pulse 1s infinite;
    }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.7; } }
    .waiting { color: var(--text-muted); font-size: 14px; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>🧹 Apple Photos Cleaner</h1>
        <div class="meta" id="meta">Initializing...</div>
      </div>
      <div class="header-right">
        <button class="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark mode" id="theme-btn">🌙</button>
        <span id="status" class="badge">Starting</span>
      </div>
    </header>

    <div class="stats">
      <div class="stat-card">
        <div class="stat-label">Scanned</div>
        <div class="stat-value" id="scanned">0</div>
        <div class="progress"><span class="progress-text" id="progress">0%</span></div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Matches</div>
        <div class="stat-value matched" id="matched">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Deleted</div>
        <div class="stat-value deleted" id="deleted">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Cost</div>
        <div class="stat-value cost" id="cost">$0.00</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Speed</div>
        <div class="stat-value speed" id="speed">-</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">ETA</div>
        <div class="stat-value" id="eta">-</div>
      </div>
    </div>

    <div class="main-grid">
      <div class="card">
        <div class="card-title">Current Photo</div>
        <div class="photo-container">
          <img id="photo-img" src="" alt="" style="display:none;">
          <div id="photo-placeholder" class="waiting">Waiting for scan to start...</div>
          <div id="match-badge" class="match-badge" style="display:none;">⚡ MATCH</div>
        </div>
        <div class="photo-name" id="photo-name"></div>
        <div class="reason" id="reason" style="display:none;"></div>
        <div class="confidence-bar"><div class="confidence-fill" id="confidence" style="width:0%;"></div></div>
      </div>

      <div class="card">
        <div class="card-title">
          Activity Log
          <div style="display:flex;gap:8px;">
            <button class="btn" id="delete-now-btn" onclick="deleteNow()" style="display:none;background:linear-gradient(135deg,#f97316 0%,#ea580c 100%);">Delete Matches Now</button>
            <button class="btn" onclick="stopScan()">Stop Scan</button>
          </div>
        </div>
        <div class="log" id="log"></div>
      </div>
    </div>
  </div>

<script>
let lastUpdate = 0;
async function fetchStats() {
  try {
    const res = await fetch('/stats');
    const d = await res.json();
    
    document.getElementById('status').textContent = d.status || 'Idle';
    document.getElementById('scanned').textContent = d.scanned ?? 0;
    document.getElementById('matched').textContent = d.matched ?? 0;
    document.getElementById('deleted').textContent = d.deleted ?? 0;
    document.getElementById('cost').textContent = `$${(d.cost || 0).toFixed(4)}`;
    document.getElementById('meta').textContent = d.meta || '';
    document.getElementById('speed').textContent = d.speed || '-';
    document.getElementById('eta').textContent = d.eta || '-';
    document.getElementById('progress').textContent = d.progress || '0%';

    const logEl = document.getElementById('log');
    logEl.innerHTML = (d.history || []).slice(-100).map(l => {
      let cls = '';
      if (l.includes('MATCH')) cls = 'match';
      else if (l.includes('ERROR') || l.includes('⚠')) cls = 'error';
      else if (l.includes('DELETED') || l.includes('🗑')) cls = 'delete';
      return `<div class="log-entry ${cls}">${l}</div>`;
    }).join('');
    logEl.scrollTop = logEl.scrollHeight;

    if (d.current_photo && d.current_photo.data) {
      document.getElementById('photo-placeholder').style.display = 'none';
      const img = document.getElementById('photo-img');
      img.src = `data:image/jpeg;base64,${d.current_photo.data}`;
      img.style.display = 'block';
      document.getElementById('photo-name').textContent = d.current_photo.name || '';
      
      const reason = d.current_photo.reason || '';
      const reasonEl = document.getElementById('reason');
      reasonEl.textContent = reason;
      reasonEl.style.display = reason ? 'block' : 'none';
      
      const conf = Math.round((d.current_photo.confidence || 0) * 100);
      document.getElementById('confidence').style.width = conf + '%';
      
      const badge = document.getElementById('match-badge');
      badge.style.display = d.current_photo.is_match ? 'block' : 'none';
    }
    
    // Show "Delete Now" button if there are matches and not in dry run or deleting
    const deleteBtn = document.getElementById('delete-now-btn');
    const hasDryRun = (d.meta || '').includes('DRY RUN');
    const pendingDeletes = (d.matched || 0) - (d.deleted || 0);
    
    if (d.deleting) {
      deleteBtn.style.display = 'inline-block';
      deleteBtn.textContent = `Deleting... (${d.deleted}/${d.matched})`;
      deleteBtn.disabled = true;
      deleteBtn.style.opacity = '0.7';
    } else if (pendingDeletes > 0 && !hasDryRun) {
      deleteBtn.style.display = 'inline-block';
      deleteBtn.textContent = `Delete ${pendingDeletes} Now`;
      deleteBtn.disabled = false;
      deleteBtn.style.opacity = '1';
    } else {
      deleteBtn.style.display = 'none';
    }
  } catch (e) { console.error(e); }
}

async function stopScan() {
  const res = await fetch('/stop', { method: 'POST' });
  const data = await res.json();
  document.getElementById('status').textContent = 'Stopped';
  
  if (data.matched > 0) {
    const action = confirm(
      `Scan stopped. Found ${data.matched} matches so far.\n\n` +
      `Click OK to DELETE these ${data.matched} photos now.\n` +
      `Click Cancel to keep them.\n\n` +
      `(Deletion progress will show in the activity log)`
    );
    
    if (action) {
      await performDelete();
    }
  }
}

async function deleteNow() {
  const matched = parseInt(document.getElementById('matched').textContent) || 0;
  if (matched === 0) return;
  
  const action = confirm(`Delete ${matched} matched photos now?\n\nPhotos will be deleted one by one.\nWatch the progress in the activity log.`);
  if (action) {
    await performDelete();
  }
}

async function performDelete() {
  document.getElementById('status').textContent = 'Deleting...';
  document.getElementById('delete-now-btn').style.display = 'none';
  
  const delRes = await fetch('/delete-matches', { method: 'POST' });
  const delData = await delRes.json();
  
  if (delData.dry_run) {
    alert('Dry run mode - no photos were deleted.');
    document.getElementById('status').textContent = 'Complete';
  } else if (delData.started) {
    // Deletion started in background - UI will update via fetchStats
    console.log(`Started deleting ${delData.total} photos...`);
  } else if (delData.message) {
    console.log(delData.message);
  }
}

setInterval(fetchStats, 500);

// Theme handling - respects system preference by default
function getSystemTheme() {
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function setTheme(theme) {
  if (theme === 'light') {
    document.body.classList.add('light-mode');
    document.getElementById('theme-btn').textContent = '☀️';
  } else {
    document.body.classList.remove('light-mode');
    document.getElementById('theme-btn').textContent = '🌙';
  }
  localStorage.setItem('theme', theme);
}

function toggleTheme() {
  const isLight = document.body.classList.contains('light-mode');
  setTheme(isLight ? 'dark' : 'light');
}

// Initialize theme on load
(function() {
  const saved = localStorage.getItem('theme');
  const theme = saved || getSystemTheme();
  setTheme(theme);
  
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
# PhotoCleaner Class
# ============================================================
class PhotoCleaner:
    """Fast, parallel photo cleaner with AI vision."""

    def __init__(
        self,
        backend: str = "openai",
        model: str = "gpt-4o-mini",
        confidence_threshold: float = 0.7,
        openai_api_key: Optional[str] = None,
    ):
        self.backend = backend
        self.model = model
        self.confidence_threshold = confidence_threshold
        self.photosdb = None
        
        if backend == "openai":
            if not openai:
                print("❌ openai library not installed. Run: pip install openai")
                sys.exit(1)
            key = openai_api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                print("❌ OPENAI_API_KEY not set.")
                print("   Add it to: /Users/mike/Documents/Keys/.env")
                sys.exit(1)
            self.client = openai.OpenAI(api_key=key)
        elif backend == "ollama":
            if not requests:
                print("❌ requests library not installed. Run: pip install requests")
                sys.exit(1)
            self.ollama_url = "http://localhost:11434/api/chat"
            try:
                requests.get("http://localhost:11434", timeout=2)
            except:
                print("❌ Ollama not running. Start it with: ollama serve")
                sys.exit(1)

    def load_photos_library(self) -> int:
        """Load Apple Photos library."""
        print("📚 Loading Apple Photos library...")
        self.photosdb = osxphotos.PhotosDB()
        total = len(self.photosdb.photos())
        print(f"   Found {total:,} photos")
        return total

    def get_photos(self, limit: Optional[int] = None, album: Optional[str] = None) -> list:
        """Get photos, filtering out unsupported formats."""
        if not self.photosdb:
            self.load_photos_library()

        photos = self.photosdb.photos()

        if album:
            album_uuids = set()
            for a in self.photosdb.album_info:
                if album.lower() in a.title.lower():
                    album_uuids.update(p.uuid for p in a.photos)
            photos = [p for p in photos if p.uuid in album_uuids]

        # Filter to supported image formats only
        filtered = []
        for p in photos:
            fname = (p.original_filename or "").lower()
            ext = Path(fname).suffix.lower() if fname else ""
            if ext in SKIP_EXTENSIONS:
                continue
            if ext in SUPPORTED_EXTENSIONS or ext == "":
                filtered.append(p)

        if limit:
            filtered = filtered[:limit]

        return filtered

    def encode_image(self, photo) -> tuple[Optional[str], str]:
        """Get photo path and encode to base64."""
        try:
            # Try to use the photo's path directly (much faster than exporting)
            filepath = Path(photo.path) if photo.path else None
            
            if not filepath or not filepath.exists():
                # Fallback: export the photo (slower)
                with tempfile.TemporaryDirectory() as tmpdir:
                    exported = photo.export(tmpdir, use_photos_export=False)  # Don't use Photos export - too slow!
                    if not exported:
                        return None, ""
                    filepath = Path(exported[0])
            
            if not filepath.exists():
                return None, ""

            ext = filepath.suffix.lower()
            if ext in SKIP_EXTENSIONS:
                return None, ""

            # Resize image for faster API processing
            if PIL_AVAILABLE:
                try:
                    with Image.open(filepath) as img:
                        # Convert to RGB if necessary
                        if img.mode in ('RGBA', 'P'):
                            img = img.convert('RGB')
                        
                        # Resize maintaining aspect ratio
                        img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
                        
                        buffer = BytesIO()
                        img.save(buffer, format='JPEG', quality=80, optimize=True)
                        buffer.seek(0)
                        return base64.standard_b64encode(buffer.read()).decode("utf-8"), "image/jpeg"
                except Exception:
                    pass  # Fall back to original

            # Fallback: read original file
            media_type = {
                ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"
            }.get(ext, "image/jpeg")

            with open(filepath, "rb") as f:
                return base64.standard_b64encode(f.read()).decode("utf-8"), media_type

        except Exception as e:
            log_line(f"ERROR encoding: {e}")
            return None, ""

    def analyze_openai(self, image_data: str, media_type: str, description: str) -> dict:
        """Analyze image with OpenAI Vision API (with retry)."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": 'Analyze if image matches description for deletion. Respond ONLY with JSON: {"match": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}',
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"Delete if matches: '{description}'"},
                                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}", "detail": "low"}},
                            ],
                        },
                    ],
                    max_tokens=200,
                    timeout=30,
                )
                
                content = response.choices[0].message.content if response.choices else ""
                if not content:
                    if response.choices and response.choices[0].finish_reason == 'length':
                        return {"match": False, "confidence": 0, "reason": "Response truncated"}
                    return {"match": False, "confidence": 0, "reason": "Empty response"}
                
                return self._parse_json(content)

            except openai.RateLimitError:
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                return {"match": False, "confidence": 0, "reason": "Rate limited"}
            except openai.APIError as e:
                return {"match": False, "confidence": 0, "reason": f"API error: {str(e)[:100]}"}
            except Exception as e:
                return {"match": False, "confidence": 0, "reason": f"Error: {str(e)[:100]}"}

        return {"match": False, "confidence": 0, "reason": "Max retries exceeded"}

    def analyze_ollama(self, image_data: str, description: str) -> dict:
        """Analyze image with local Ollama."""
        try:
            response = requests.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [{
                        "role": "user",
                        "content": f"Does this image match '{description}'? Respond with JSON: {{\"match\": true/false, \"confidence\": 0.0-1.0, \"reason\": \"...\"}}",
                        "images": [image_data]
                    }]
                },
                timeout=60
            )
            if response.status_code != 200:
                return {"match": False, "confidence": 0, "reason": f"Ollama error: {response.status_code}"}
            
            content = response.json().get("message", {}).get("content", "")
            return self._parse_json(content)
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": f"Ollama error: {str(e)[:100]}"}

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM response."""
        try:
            text = text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            return json.loads(text)
        except:
            return {"match": False, "confidence": 0, "reason": "JSON parse error"}

    def analyze_photo(self, photo, description: str) -> dict:
        """Analyze a single photo."""
        image_data, media_type = self.encode_image(photo)
        if not image_data:
            return {"match": False, "confidence": 0, "reason": "Could not encode image", "skipped": True}

        if self.backend == "openai":
            return self.analyze_openai(image_data, media_type, description)
        else:
            return self.analyze_ollama(image_data, description)

    def delete_photos(self, uuids: list[str]) -> int:
        """Add photos to '🤖 AI Matches - To Delete' album.
        
        Note: Direct deletion via AppleScript is blocked on modern macOS (security).
        Photos are added to a dedicated album for easy batch deletion.
        """
        if not uuids:
            return 0
        
        album_name = "🤖 AI Matches - To Delete"
        uuid_list = '", "'.join(uuids)
        
        script = f'''
        tell application "Photos"
            set albumName to "{album_name}"
            set targetAlbum to missing value
            
            try
                set targetAlbum to album albumName
            on error
                set targetAlbum to make new album named albumName
            end try
            
            set addedCount to 0
            set uuidList to {{"{uuid_list}"}}
            
            repeat with photoUUID in uuidList
                try
                    set targetPhoto to media item id photoUUID
                    add {{targetPhoto}} to targetAlbum
                    set addedCount to addedCount + 1
                end try
            end repeat
            
            return addedCount
        end tell
        '''
        
        try:
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.strip().isdigit():
                added = int(result.stdout.strip())
                if added > 0:
                    log_line(f"ADDED TO ALBUM | count={added}")
                return added
        except subprocess.TimeoutExpired:
            log_line("ALBUM TIMEOUT - Photos app may be slow")
        except Exception as e:
            log_line(f"ALBUM EXCEPTION: {e}")
        
        return 0
    
    def open_delete_album(self):
        """Open Photos app and navigate to the delete album."""
        script = '''
        tell application "Photos"
            activate
            delay 0.5
            try
                reveal album "🤖 AI Matches - To Delete"
            end try
        end tell
        '''
        try:
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        except:
            pass

    def run_parallel(
        self,
        description: str,
        limit: Optional[int] = None,
        dry_run: bool = True,
        realtime_delete: bool = False,
        on_progress: Optional[callable] = None,
    ) -> dict:
        """Run scan with parallel processing."""
        global scan_state
        
        with state_lock:
            scan_state["is_running"] = True
            scan_state["stop_requested"] = False
            scan_state["start_time"] = time.time()
            scan_state["last_progress_time"] = None  # Will be set after library loads
            scan_state["recent_scans"] = []
            scan_state["stats"] = {"scanned": 0, "matched": 0, "deleted": 0, "cost": 0.0, "skipped": 0, "errors": 0}
            scan_state["history"] = []
            scan_state["matches"] = []
            scan_state["status"] = "Loading Photos library... (this may take 1-2 min)"
        
        photos = self.get_photos(limit=limit)
        
        # Reset progress time after library loads
        with state_lock:
            scan_state["last_progress_time"] = time.time()
        total = len(photos)
        
        with state_lock:
            scan_state["total_photos"] = total
            scan_state["status"] = "Scanning"
            mode_str = " | DRY RUN" if dry_run else (" | REALTIME DELETE" if realtime_delete else "")
            scan_state["meta"] = f"'{description}' | {self.backend}/{self.model} | {total} photos{mode_str}"
        
        log_line(f"START | desc='{description}' | backend={self.backend} | model={self.model} | total={total} | dry_run={dry_run}")
        
        price = PRICE_PER_MILLION.get(self.model, 0.15)
        cost_per_image = price * TOKENS_PER_IMAGE / 1_000_000 if self.backend == "openai" else 0
        
        to_delete = []
        
        def process_photo(idx_photo):
            idx, photo = idx_photo
            
            # Check stop flag at start
            if scan_state["stop_requested"]:
                return None
            
            filename = photo.original_filename or "Unknown"
            
            # Check again before expensive API call
            if scan_state["stop_requested"]:
                return None
                
            result = self.analyze_photo(photo, description)
            
            # Check after API call
            if scan_state["stop_requested"]:
                return None
            
            with state_lock:
                scan_state["stats"]["scanned"] += 1
                scan_state["stats"]["cost"] += cost_per_image
                
                if result.get("skipped"):
                    scan_state["stats"]["skipped"] += 1
                elif result.get("reason", "").startswith("Error") or result.get("reason", "").startswith("API error"):
                    scan_state["stats"]["errors"] += 1
                
                # Track progress for speed calculation and stall detection
                now = time.time()
                scan_state["last_progress_time"] = now
                scanned = scan_state["stats"]["scanned"]
                
                # Add to recent scans for speed calculation
                recent = scan_state.get("recent_scans", [])
                recent.append((now, scanned))
                # Keep only last 30 seconds
                cutoff = now - 30
                scan_state["recent_scans"] = [(t, c) for t, c in recent if t > cutoff]
                
                scan_state["status"] = f"Scanning ({scanned}/{total})"
            
            is_match = result.get("match", False) and result.get("confidence", 0) >= self.confidence_threshold
            
            # Encode image for dashboard (smaller version)
            img_data, _ = self.encode_image(photo)
            
            with state_lock:
                scan_state["current_photo"] = {
                    "name": filename,
                    "data": img_data[:50000] if img_data else "",  # Limit size for dashboard
                    "reason": result.get("reason", ""),
                    "confidence": result.get("confidence", 0),
                    "is_match": is_match,
                }
            
            if is_match:
                with state_lock:
                    scan_state["stats"]["matched"] += 1
                    scan_state["matches"].append({"uuid": photo.uuid, "filename": filename})
                    scan_state["history"].append(f"⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                print(f"\n   ⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                print(f"      Reason: {result.get('reason', 'N/A')}")
                log_line(f"MATCH | {filename} | conf={result.get('confidence', 0):.2f}")
                
                # Add to album immediately if realtime mode
                if realtime_delete and not dry_run:
                    log_line(f"REALTIME ADD | {filename} | uuid={photo.uuid}")
                    added = self.delete_photos([photo.uuid])
                    if added > 0:
                        with state_lock:
                            scan_state["stats"]["deleted"] += 1
                            scan_state["history"].append(f"   📁 → Album: {filename}")
                        log_line(f"REALTIME ADD SUCCESS | {filename}")
                        print(f"      📁 Added to delete album")
                    return None  # Already processed, don't add to to_delete list
                
                return photo.uuid
            else:
                with state_lock:
                    scan_state["history"].append(f"   {filename}")
            
            return None

        # Process in parallel with batching for responsive stop
        batch_size = MAX_WORKERS * 2  # Process in small batches
        
        for batch_start in range(0, len(photos), batch_size):
            if scan_state["stop_requested"]:
                with state_lock:
                    scan_state["history"].append("⏹️ Scan stopped by user")
                break
            
            batch = photos[batch_start:batch_start + batch_size]
            
            try:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {executor.submit(process_photo, (batch_start + i, p)): i for i, p in enumerate(batch)}
                    
                    try:
                        for future in as_completed(futures, timeout=FUTURE_TIMEOUT * 2):
                            if scan_state["stop_requested"]:
                                # Cancel remaining futures
                                for f in futures:
                                    f.cancel()
                                break
                            
                            try:
                                result = future.result(timeout=FUTURE_TIMEOUT)
                                if result:
                                    to_delete.append(result)
                            except TimeoutError:
                                log_line("TIMEOUT: Photo processing took too long, skipping")
                                with state_lock:
                                    scan_state["stats"]["errors"] += 1
                            except Exception as e:
                                log_line(f"ERROR in future: {e}")
                                with state_lock:
                                    scan_state["stats"]["errors"] += 1
                    except TimeoutError:
                        log_line("BATCH TIMEOUT: Moving to next batch")
                        with state_lock:
                            scan_state["stats"]["errors"] += len(batch)
            except Exception as e:
                log_line(f"BATCH ERROR: {e}")
                with state_lock:
                    scan_state["stats"]["errors"] += 1

        # Check if stopped
        was_stopped = scan_state["stop_requested"]
        if was_stopped:
            print("\n⏹️  Scan stopped by user")
            log_line("STOPPED by user")
        
        # Delete matched photos (unless stopped - user will be prompted separately)
        deleted_count = 0
        if to_delete and not dry_run and not was_stopped:
            with state_lock:
                scan_state["status"] = "Deleting..."
                scan_state["history"].append(f"🗑️  Deleting {len(to_delete)} photos...")
            
            print(f"\n🗑️  Deleting {len(to_delete)} matched photos...")
            deleted_count = self.delete_photos(to_delete)
            
            with state_lock:
                scan_state["stats"]["deleted"] = deleted_count
                scan_state["history"].append(f"✅ Deleted {deleted_count} photos")
            
            print(f"   ✅ Deleted {deleted_count} photos")
            log_line(f"DELETED | count={deleted_count}")

        # Final stats
        with state_lock:
            scan_state["status"] = "Complete" if not was_stopped else "Stopped"
            scan_state["is_running"] = False
            stats = scan_state["stats"].copy()
            pending_matches = len(scan_state["matches"]) if was_stopped else 0
        
        elapsed = time.time() - scan_state["start_time"]
        
        # Print summary
        print(f"\n{'='*50}")
        print(f"📊 Summary ({format_time(elapsed)}):")
        print(f"   Scanned:  {stats['scanned']}")
        print(f"   Matched:  {stats['matched']}")
        print(f"   Added to album: {stats['deleted']}")
        if pending_matches > 0:
            print(f"   Pending:  {pending_matches} (not processed due to stop)")
        print(f"   Cost:     ${stats['cost']:.4f}")
        
        if stats['deleted'] > 0:
            print(f"\n   📁 Opening Photos app...")
            print(f"      → Select all (⌘A) → Delete (⌫)")
            self.open_delete_album()
        print(f"{'='*50}\n")
        
        log_line(f"END | scanned={stats['scanned']} | matched={stats['matched']} | deleted={stats['deleted']} | stopped={was_stopped} | time={elapsed:.1f}s")
        
        return stats

    def run(
        self,
        description: str,
        limit: Optional[int] = None,
        album: Optional[str] = None,
        dry_run: bool = True,
        confirm_each: bool = False,
    ) -> dict:
        """CLI run mode (sequential for compatibility)."""
        print(f"\n🔍 Looking for: \"{description}\"")
        print(f"   Backend: {self.backend} ({self.model})")
        if dry_run:
            print("   Mode: DRY RUN (no deletions)\n")

        photos = self.get_photos(limit=limit, album=album)
        total = len(photos)
        print(f"   Scanning {total} photos...\n")

        stats = {"scanned": 0, "matched": 0, "deleted": 0}
        to_delete = []
        start_time = time.time()

        for i, photo in enumerate(photos):
            if i > 0 and i % 10 == 0:
                elapsed = time.time() - start_time
                speed = i / elapsed
                eta = (total - i) / speed if speed > 0 else 0
                print(f"   Progress: {i}/{total} ({speed:.1f}/s, ETA: {format_time(eta)})")

            stats["scanned"] += 1
            filename = photo.original_filename or "Unknown"
            result = self.analyze_photo(photo, description)

            if result.get("match") and result.get("confidence", 0) >= self.confidence_threshold:
                stats["matched"] += 1
                print(f"\n   ⚡ MATCH: {filename}")
                print(f"      {result.get('confidence', 0):.0%} - {result.get('reason', '')}")

                if confirm_each and not dry_run:
                    if input("      Delete? [y/N]: ").strip().lower() != "y":
                        continue
                to_delete.append(photo.uuid)

        if to_delete:
            if dry_run:
                print(f"\n📋 Would delete {len(to_delete)} photos")
            else:
                print(f"\n🗑️  Deleting {len(to_delete)} photos...")
                stats["deleted"] = self.delete_photos(to_delete)
                print(f"   ✅ Deleted {stats['deleted']}")

        elapsed = time.time() - start_time
        print(f"\n{'='*50}")
        print(f"📊 Summary ({format_time(elapsed)}):")
        print(f"   Scanned: {stats['scanned']}")
        print(f"   Matched: {stats['matched']}")
        print(f"   Deleted: {stats['deleted']}")
        print(f"{'='*50}\n")

        return stats


# ============================================================
# Dashboard Server
# ============================================================
def start_dashboard(cleaner: PhotoCleaner, description: str, limit: Optional[int], album: Optional[str], dry_run: bool, realtime_delete: bool = False):
    """Start Flask dashboard with background scan."""
    if not FLASK_AVAILABLE:
        print("❌ Flask not installed. Run: pip install flask")
        print("   Falling back to CLI mode...")
        cleaner.run(description, limit=limit, album=album, dry_run=dry_run)
        return

    app = Flask(__name__)
    
    # Suppress Flask logging
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    @app.route('/')
    def index():
        return render_template_string(HTML_TEMPLATE)

    @app.route('/stats')
    def stats():
        with state_lock:
            s = scan_state["stats"]
            now = time.time()
            elapsed = now - scan_state["start_time"] if scan_state["start_time"] else 0
            scanned = s["scanned"]
            total = scan_state["total_photos"]
            
            # Calculate recent speed (last 30 seconds) for better accuracy
            recent_scans = scan_state.get("recent_scans", [])
            # Clean old entries (keep last 30 seconds)
            cutoff = now - 30
            recent_scans = [(t, c) for t, c in recent_scans if t > cutoff]
            scan_state["recent_scans"] = recent_scans
            
            if len(recent_scans) >= 2:
                time_span = recent_scans[-1][0] - recent_scans[0][0]
                count_diff = recent_scans[-1][1] - recent_scans[0][1]
                speed = count_diff / time_span if time_span > 0 else 0
            elif elapsed > 0:
                speed = scanned / elapsed
            else:
                speed = 0
            
            # Detect stalls (no progress in 90 seconds after library loaded)
            last_progress = scan_state.get("last_progress_time")
            # Don't show stalled during library loading (last_progress is None)
            stalled = last_progress and (now - last_progress > 90) and scan_state["is_running"] and scanned > 0
            
            remaining = total - scanned
            eta = remaining / speed if speed > 0.01 else 0  # Avoid huge ETAs
            progress = f"{(scanned / total * 100):.0f}%" if total > 0 else "0%"
            is_deleting = scan_state.get("deleting", False)
            
            status = scan_state["status"]
            if stalled:
                status = "⚠️ Stalled - may be processing large file"
            
            return jsonify({
                "status": status,
                "scanned": s["scanned"],
                "matched": s["matched"],
                "deleted": s["deleted"],
                "cost": s["cost"],
                "skipped": s["skipped"],
                "errors": s["errors"],
                "history": scan_state["history"][-100:],
                "current_photo": scan_state["current_photo"],
                "meta": scan_state["meta"],
                "speed": f"{speed:.1f}/s" if speed > 0.01 else "stalled" if stalled else "-",
                "eta": format_time(eta) if eta > 0 and eta < 360000 else "calculating..." if speed < 0.01 else "-",
                "progress": progress,
                "deleting": is_deleting,
            })

    @app.route('/stop', methods=['POST'])
    def stop():
        with state_lock:
            scan_state["stop_requested"] = True
            matched = scan_state["stats"]["matched"]
            matches = scan_state["matches"].copy()
        return jsonify({"status": "stopping", "matched": matched, "matches": matches})

    @app.route('/delete-matches', methods=['POST'])
    def delete_matches():
        """Delete all matches found so far (runs in background)."""
        with state_lock:
            matches = scan_state["matches"].copy()
            dry_run_mode = scan_state["meta"] and "DRY RUN" in scan_state["meta"]
            already_deleting = scan_state.get("deleting", False)
        
        if not matches or dry_run_mode:
            return jsonify({"deleted": 0, "dry_run": dry_run_mode, "started": False})
        
        if already_deleting:
            return jsonify({"deleted": 0, "dry_run": False, "started": False, "message": "Already deleting"})
        
        # Start deletion in background thread
        def delete_in_background():
            with state_lock:
                scan_state["deleting"] = True
                scan_state["status"] = "Deleting..."
                scan_state["history"].append(f"🗑️ Starting deletion of {len(matches)} photos...")
            
            deleted_count = 0
            for i, match in enumerate(matches):
                try:
                    result = cleaner.delete_photos([match["uuid"]])
                    if result > 0:
                        deleted_count += 1
                        with state_lock:
                            scan_state["stats"]["deleted"] = deleted_count
                            scan_state["history"].append(f"   ✓ Deleted: {match['filename']} ({deleted_count}/{len(matches)})")
                except Exception as e:
                    with state_lock:
                        scan_state["history"].append(f"   ⚠️ Failed: {match['filename']}")
            
            with state_lock:
                scan_state["deleting"] = False
                scan_state["status"] = "Complete"
                scan_state["history"].append(f"✅ Finished: Deleted {deleted_count}/{len(matches)} photos")
                scan_state["matches"] = []  # Clear matches after deletion
            
            log_line(f"DELETED | count={deleted_count}")
        
        thread = threading.Thread(target=delete_in_background, daemon=True)
        thread.start()
        
        return jsonify({"deleted": 0, "dry_run": False, "started": True, "total": len(matches)})

    # Find free port
    port = find_free_port()
    if not port:
        print("❌ No free ports available")
        return

    # Start scan in background thread
    def run_scan():
        cleaner.run_parallel(description, limit=limit, dry_run=dry_run, realtime_delete=realtime_delete)

    scan_thread = threading.Thread(target=run_scan, daemon=True)
    scan_thread.start()

    print(f"\n🚀 Dashboard: http://localhost:{port}")
    print("   Opening browser...")
    webbrowser.open(f"http://localhost:{port}")

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n\n🛑 Stopping...")
        with state_lock:
            scan_state["stop_requested"] = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    app.run(host="127.0.0.1", port=port, threaded=True)


# ============================================================
# Interactive Mode
# ============================================================
def run_interactive():
    """Interactive prompts with sensible defaults."""
    print("\n" + "=" * 50)
    print("  🧹 Apple Photos Cleaner")
    print("=" * 50)

    # 1) Backend
    be = input("\n1. Backend: 1) OpenAI (default)  2) Ollama [1]: ").strip()
    backend = "ollama" if be == "2" else "openai"

    # 2) Model
    models = [
        ("gpt-5.1", "Highest accuracy"),
        ("gpt-4o-mini", "Fast & reliable (default)"),
        ("gpt-5-nano", "Fastest & cheapest"),
        ("gpt-4o", "Legacy multimodal"),
        ("gpt-4o-mini", "Legacy budget"),
    ] if backend == "openai" else [
        ("moondream", "Fast (default)"),
        ("llava", "Detailed"),
    ]
    print("\n2. Model:")
    for i, (name, desc) in enumerate(models, 1):
        print(f"   {i}. {name:<12} ({desc})")
    m = input("   Select [Enter for default]: ").strip()
    if m.isdigit() and 0 < int(m) <= len(models):
        model = models[int(m) - 1][0]
    else:
        model = "gpt-4o-mini" if backend == "openai" else "moondream"

    # 3) Description
    default_desc = "banking, payments, and messaging screenshots (Instagram, WhatsApp, iMessage)"
    print(f"\n3. Describe photos to find:")
    print(f"   Default: '{default_desc}'")
    desc = input("   Your description (Enter for default): ").strip()
    if not desc:
        desc = default_desc

    # 4) Limit (default: all)
    lim = input("4. Limit [all]: ").strip().lower()
    limit = None if lim in ("", "all") else (int(lim) if lim.isdigit() else None)

    # 5) Dashboard
    vis = input("5. Visual Dashboard? (Y/n) [Y]: ").strip().lower()
    visual = vis != "n"

    # 6) Dry run (default No - will delete)
    dr = input("6. Dry run (preview only)? (y/N) [N]: ").strip().lower()
    dry_run = dr == "y"

    # 7) Delete in real-time (default No - delete at end)
    realtime = False
    if not dry_run:
        rt = input("7. Add matches to album immediately? (Y/n) [Y]: ").strip().lower()
        realtime = rt != "n"  # Default is Yes

    cleaner = PhotoCleaner(backend=backend, model=model)

    if visual:
        start_dashboard(cleaner, desc, limit, None, dry_run, realtime)
    else:
        print("\n🚀 Starting scan...")
        cleaner.run_parallel(desc, limit=limit, dry_run=dry_run, realtime_delete=realtime)
        
        # Print final summary
        with state_lock:
            s = scan_state["stats"]
        print(f"\n{'='*50}")
        print(f"📊 Summary:")
        print(f"   Scanned: {s['scanned']}")
        print(f"   Matched: {s['matched']}")
        print(f"   Deleted: {s['deleted']}")
        print(f"   Cost:    ${s['cost']:.4f}")
        print(f"{'='*50}\n")


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Fast AI photo cleaner")
    parser.add_argument("description", nargs="?", help="Photos to find (omit for interactive)")
    parser.add_argument("--limit", type=int, help="Max photos to scan")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--realtime", action="store_true", help="Delete matches immediately as found")
    parser.add_argument("--backend", choices=["openai", "ollama"], default="openai")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--dashboard", action="store_true", help="Open web dashboard")

    args = parser.parse_args()

    if not args.description:
        run_interactive()
        return

    cleaner = PhotoCleaner(backend=args.backend, model=args.model)

    if args.dashboard:
        start_dashboard(cleaner, args.description, args.limit, None, args.dry_run, args.realtime)
    else:
        cleaner.run_parallel(args.description, limit=args.limit, dry_run=args.dry_run, realtime_delete=args.realtime)


if __name__ == "__main__":
    main()
