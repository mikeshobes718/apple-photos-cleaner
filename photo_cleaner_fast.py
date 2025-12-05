#!/usr/bin/env python3
"""
Apple Photos Cleaner - FAST VERSION
Uses AppleScript instead of osxphotos for instant startup.
No database loading - gets photos directly from Photos.app.
"""

import os
import sys
import json
import time
import base64
import subprocess
import argparse
import threading
import webbrowser
from pathlib import Path
from io import BytesIO
from typing import Optional
from datetime import datetime

# Setup
LOG_DIR = os.path.expanduser("~/Documents/logs")
os.makedirs(LOG_DIR, exist_ok=True)

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(f"{LOG_DIR}/photo_cleaner.log", "a") as f:
        f.write(line + "\n")

# Load API key
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH) as f:
        for k, v in json.load(f).items():
            os.environ.setdefault(k, v)

# Imports
try:
    import openai
except ImportError:
    print("❌ Run: pip install openai")
    sys.exit(1)

try:
    from PIL import Image
    from pillow_heif import register_heif_opener
    register_heif_opener()
    PIL_OK = True
except:
    PIL_OK = False

try:
    from flask import Flask, jsonify, render_template_string
    FLASK_OK = True
except:
    FLASK_OK = False

# Config
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_DESC = "banking, payments, and messaging screenshots (Instagram, WhatsApp, iMessage)"
MAX_SIZE = 512
SKIP_EXT = {".mov", ".mp4", ".m4v", ".avi", ".raw", ".cr2", ".nef", ".arw", ".dng", ".3gp"}
COST_PER_IMG = 0.15 * 1100 / 1_000_000  # gpt-4o-mini

# Global state
state = {
    "status": "idle", "scanned": 0, "matched": 0, "deleted": 0, 
    "skipped": 0, "total": 0, "cost": 0.0, "errors": 0,
    "current_photo": None, "history": [], "matches": [],
    "is_running": False, "stop_requested": False
}
state_lock = threading.Lock()

# ============================================================
# AppleScript helpers (instant, no DB load)
# ============================================================
def get_photo_count() -> int:
    """Get total photo count via AppleScript (with retry)."""
    script = 'tell application "Photos" to return count of media items'
    for attempt in range(3):
        try:
            result = subprocess.run(["osascript", "-e", script], 
                                   capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip())
            # Photos might be stuck, try restarting
            if attempt < 2:
                log("Photos.app not responding, restarting...")
                subprocess.run(["killall", "Photos"], capture_output=True)
                time.sleep(2)
                subprocess.run(["open", "-a", "Photos"], capture_output=True)
                time.sleep(5)
        except subprocess.TimeoutExpired:
            if attempt < 2:
                log("Photos.app timed out, restarting...")
                subprocess.run(["killall", "Photos"], capture_output=True)
                time.sleep(2)
                subprocess.run(["open", "-a", "Photos"], capture_output=True)
                time.sleep(5)
        except:
            pass
    return 0

def get_photo_info(index: int) -> dict:
    """Get info for photo at index (1-based) via AppleScript."""
    script = f'''
    tell application "Photos"
        set p to media item {index}
        set photoId to id of p
        set photoName to filename of p
        set photoDate to date of p
        return photoId & "|" & photoName & "|" & (photoDate as string)
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            parts = result.stdout.strip().split("|")
            return {"id": parts[0], "name": parts[1], "date": parts[2]} if len(parts) >= 3 else None
    except:
        pass
    return None

def export_photo(photo_id: str, dest_dir: str) -> Optional[str]:
    """Export photo to dest_dir, return path."""
    script = f'''
    tell application "Photos"
        set p to media item id "{photo_id}"
        set exportPath to POSIX file "{dest_dir}"
        export {{p}} to exportPath
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            # Find exported file
            for f in Path(dest_dir).iterdir():
                return str(f)
    except:
        pass
    return None

def add_to_album(photo_id: str, album_name: str = "🤖 AI Matches - To Delete") -> bool:
    """Add photo to album."""
    script = f'''
    tell application "Photos"
        set albumName to "{album_name}"
        try
            set targetAlbum to album albumName
        on error
            set targetAlbum to make new album named albumName
        end try
        add {{media item id "{photo_id}"}} to targetAlbum
        return "ok"
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except:
        return False

# ============================================================
# Image processing
# ============================================================
def encode_image(filepath: str) -> tuple[Optional[str], str]:
    """Encode image to base64."""
    try:
        ext = Path(filepath).suffix.lower()
        if ext in SKIP_EXT:
            return None, f"skip_{ext}"
        
        if PIL_OK:
            with Image.open(filepath) as img:
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                img.thumbnail((MAX_SIZE, MAX_SIZE), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format='JPEG', quality=80)
                buf.seek(0)
                return base64.b64encode(buf.read()).decode(), "image/jpeg"
        else:
            with open(filepath, "rb") as f:
                return base64.b64encode(f.read()).decode(), "image/jpeg"
    except Exception as e:
        return None, f"error_{e}"

# ============================================================
# AI Analysis
# ============================================================
def analyze(client, model: str, image_data: str, description: str) -> dict:
    """Analyze image with OpenAI."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": 'Reply ONLY with JSON: {"match": true/false, "confidence": 0.0-1.0, "reason": "brief"}'},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Does this match: '{description}'?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}", "detail": "low"}}
                ]}
            ],
            max_tokens=150,
            timeout=30
        )
        content = response.choices[0].message.content or ""
        if "```" in content:
            content = content.split("```")[1].replace("json", "")
        return json.loads(content.strip())
    except Exception as e:
        return {"match": False, "confidence": 0, "reason": str(e)[:50]}

# ============================================================
# Main scanner
# ============================================================
def scan(description: str, limit: Optional[int] = None, dry_run: bool = False, 
         realtime: bool = True, model: str = DEFAULT_MODEL):
    """Scan photos using AppleScript (instant start)."""
    global state
    
    if not os.getenv("OPENAI_API_KEY"):
        print(f"❌ Set OPENAI_API_KEY in {ENV_PATH}")
        return
    
    client = openai.OpenAI()
    
    with state_lock:
        state.update({
            "status": "Getting photo count...",
            "scanned": 0, "matched": 0, "deleted": 0, "skipped": 0,
            "total": 0, "cost": 0.0, "errors": 0,
            "current_photo": None, "history": [], "matches": [],
            "is_running": True, "stop_requested": False,
            "description": description, "model": model
        })
    
    # Get count instantly
    total = get_photo_count()
    actual_total = min(limit, total) if limit else total
    
    with state_lock:
        state["total"] = actual_total
        state["status"] = "Scanning"
        state["history"].append(f"📚 Found {total:,} photos, scanning {actual_total:,}")
    
    log(f"START | desc='{description}' | model={model} | limit={actual_total}/{total}")
    print(f"\n🔍 Scanning {actual_total:,} photos for: \"{description}\"")
    print(f"   Model: {model} | Dry run: {dry_run}\n")
    
    import tempfile
    
    for i in range(1, actual_total + 1):
        if state["stop_requested"]:
            log("STOPPED by user")
            break
        
        # Get photo info
        info = get_photo_info(i)
        if not info:
            with state_lock:
                state["errors"] += 1
                state["scanned"] += 1
            continue
        
        filename = info["name"]
        photo_id = info["id"]
        
        # Export and encode
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = export_photo(photo_id, tmpdir)
            if not filepath:
                with state_lock:
                    state["errors"] += 1
                    state["scanned"] += 1
                continue
            
            img_data, result_type = encode_image(filepath)
        
        if not img_data:
            with state_lock:
                if result_type.startswith("skip_"):
                    state["skipped"] += 1
                else:
                    state["errors"] += 1
                state["scanned"] += 1
                state["history"].append(f"   ⏭️ {filename} ({result_type})")
            continue
        
        # Analyze
        result = analyze(client, model, img_data, description)
        
        with state_lock:
            state["scanned"] += 1
            state["cost"] += COST_PER_IMG
            state["status"] = f"Scanning ({i}/{actual_total})"
            
            is_match = result.get("match", False) and result.get("confidence", 0) >= 0.7
            
            state["current_photo"] = {
                "name": filename,
                "data": img_data[:50000],
                "reason": result.get("reason", ""),
                "confidence": result.get("confidence", 0),
                "is_match": is_match
            }
            
            if is_match:
                state["matched"] += 1
                state["matches"].append({"id": photo_id, "filename": filename})
                state["history"].append(f"⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                
                log(f"MATCH | {filename}")
                print(f"   ⚡ MATCH: {filename} ({result.get('confidence', 0):.0%})")
                
                if realtime and not dry_run:
                    if add_to_album(photo_id):
                        state["deleted"] += 1
                        log(f"ADDED TO ALBUM | {filename}")
                        print(f"      📁 Added to album")
            else:
                state["history"].append(f"   {filename}")
            
            if len(state["history"]) > 100:
                state["history"] = state["history"][-50:]
    
    with state_lock:
        state["status"] = "Complete" if not state["stop_requested"] else "Stopped"
        state["is_running"] = False
    
    print(f"\n{'='*50}")
    print(f"📊 Summary:")
    print(f"   Scanned: {state['scanned']}")
    print(f"   Matches: {state['matched']}")
    print(f"   In Album: {state['deleted']}")
    print(f"   Skipped: {state['skipped']}")
    print(f"   Errors: {state['errors']}")
    print(f"   Cost: ${state['cost']:.4f}")
    print(f"{'='*50}\n")
    
    if state["deleted"] > 0:
        print("📁 Review in Photos → Albums → '🤖 AI Matches - To Delete'")
        subprocess.run(["osascript", "-e", 'tell application "Photos" to activate'], capture_output=True)

# ============================================================
# Dashboard
# ============================================================
HTML = '''<!DOCTYPE html>
<html><head>
<title>Photo Cleaner</title>
<style>
:root { --bg: #0f172a; --card: #1e293b; --text: #f1f5f9; --accent: #3b82f6; --success: #22c55e; --warning: #f59e0b; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui; background: var(--bg); color: var(--text); padding: 20px; }
.container { max-width: 1200px; margin: 0 auto; }
h1 { margin-bottom: 10px; }
.subtitle { color: #94a3b8; margin-bottom: 20px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 15px; margin-bottom: 20px; }
.stat { background: var(--card); padding: 15px; border-radius: 8px; text-align: center; }
.stat-label { font-size: 12px; color: #94a3b8; }
.stat-value { font-size: 24px; font-weight: bold; margin-top: 5px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.card { background: var(--card); border-radius: 12px; padding: 20px; }
.photo-img { width: 100%; max-height: 300px; object-fit: contain; border-radius: 8px; background: #000; }
.log { height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; background: #0f172a; padding: 10px; border-radius: 8px; }
.log-entry { padding: 3px 0; }
.btn { padding: 10px 20px; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; }
.btn-danger { background: #ef4444; color: white; }
.btn-success { background: var(--success); color: white; }
.match { color: var(--warning); }
</style>
</head>
<body>
<div class="container">
<h1>🧹 Apple Photos Cleaner (Fast)</h1>
<div class="subtitle" id="subtitle">Starting...</div>
<div class="stats">
  <div class="stat"><div class="stat-label">Status</div><div class="stat-value" id="status">-</div></div>
  <div class="stat"><div class="stat-label">Scanned</div><div class="stat-value" id="scanned">0</div></div>
  <div class="stat"><div class="stat-label">Matches</div><div class="stat-value match" id="matched">0</div></div>
  <div class="stat"><div class="stat-label">In Album</div><div class="stat-value" id="deleted">0</div></div>
  <div class="stat"><div class="stat-label">Skipped</div><div class="stat-value" id="skipped">0</div></div>
  <div class="stat"><div class="stat-label">Cost</div><div class="stat-value" id="cost">$0</div></div>
</div>
<div class="grid">
  <div class="card">
    <h3>Current Photo</h3>
    <img id="photo" class="photo-img" src="">
    <div id="photo-name" style="margin-top:10px"></div>
    <div id="photo-reason" style="color:#94a3b8;font-size:14px"></div>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h3>Activity</h3>
      <button class="btn btn-danger" onclick="fetch('/stop',{method:'POST'})">Stop</button>
    </div>
    <div class="log" id="log"></div>
    <button class="btn btn-success" style="margin-top:10px;width:100%" onclick="fetch('/open-album',{method:'POST'})">Open Album in Photos</button>
  </div>
</div>
</div>
<script>
function update() {
  fetch('/stats').then(r=>r.json()).then(d=>{
    document.getElementById('status').textContent = d.status;
    document.getElementById('scanned').textContent = d.scanned + (d.total ? '/' + d.total : '');
    document.getElementById('matched').textContent = d.matched;
    document.getElementById('deleted').textContent = d.deleted;
    document.getElementById('skipped').textContent = d.skipped;
    document.getElementById('cost').textContent = '$' + (d.cost||0).toFixed(4);
    document.getElementById('subtitle').textContent = d.description ? `"${d.description}" | ${d.model}` : 'Starting...';
    
    if (d.current_photo && d.current_photo.data) {
      document.getElementById('photo').src = 'data:image/jpeg;base64,' + d.current_photo.data;
      document.getElementById('photo-name').textContent = d.current_photo.name;
      document.getElementById('photo-reason').textContent = d.current_photo.reason;
    }
    
    const log = document.getElementById('log');
    log.innerHTML = (d.history||[]).slice(-50).map(h => 
      '<div class="log-entry' + (h.includes('MATCH') ? ' match' : '') + '">' + h + '</div>'
    ).join('');
    log.scrollTop = log.scrollHeight;
  });
}
setInterval(update, 1000);
update();
</script>
</body></html>'''

def create_app(desc, limit, dry_run, realtime, model):
    app = Flask(__name__)
    
    @app.route('/')
    def index():
        return render_template_string(HTML)
    
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
        subprocess.run(["osascript", "-e", 'tell application "Photos" to activate'], capture_output=True)
        return jsonify({"ok": True})
    
    def run():
        scan(desc, limit, dry_run, realtime, model)
    
    threading.Thread(target=run, daemon=True).start()
    return app

def find_port():
    import socket
    for p in range(5000, 5020):
        try:
            with socket.socket() as s:
                s.bind(('localhost', p))
                return p
        except:
            pass
    return 5000

# ============================================================
# Interactive
# ============================================================
def interactive():
    print("\n" + "="*50)
    print("  🧹 Apple Photos Cleaner (FAST)")
    print("  No database loading - instant start!")
    print("="*50 + "\n")
    
    desc = input(f"1. What to find [{DEFAULT_DESC[:50]}...]:\n   ").strip() or DEFAULT_DESC
    
    lim = input("\n2. How many photos to scan [all]: ").strip().lower()
    limit = None if lim in ("", "all") else (int(lim) if lim.isdigit() else None)
    
    dashboard = input("\n3. Open dashboard? (Y/n) [Y]: ").strip().lower() != "n"
    dry_run = input("\n4. Dry run? (y/N) [N]: ").strip().lower() == "y"
    realtime = True
    if not dry_run:
        realtime = input("\n5. Add to album immediately? (Y/n) [Y]: ").strip().lower() != "n"
    
    if dashboard and FLASK_OK:
        port = find_port()
        print(f"\n🚀 Dashboard: http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")
        app = create_app(desc, limit, dry_run, realtime, DEFAULT_MODEL)
        app.run(host='localhost', port=port, threaded=True)
    else:
        scan(desc, limit, dry_run, realtime)

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast Apple Photos cleaner (no DB load)")
    parser.add_argument("description", nargs="?")
    parser.add_argument("--limit", type=int, default=None, help="Limit photos (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--no-realtime", action="store_true")
    
    args = parser.parse_args()
    
    if not args.description:
        interactive()
    elif args.dashboard and FLASK_OK:
        port = find_port()
        print(f"🚀 Dashboard: http://localhost:{port}")
        webbrowser.open(f"http://localhost:{port}")
        app = create_app(args.description, args.limit, args.dry_run, not args.no_realtime, DEFAULT_MODEL)
        app.run(host='localhost', port=port, threaded=True)
    else:
        scan(args.description, args.limit, args.dry_run, not args.no_realtime)

