#!/usr/bin/env python3.12
"""
Apple Photos Cleaner - Delete photos matching a description using AI vision.
Supports OpenAI (Cloud) and Ollama (Local/Free).

Usage:
    python photo_cleaner.py "blurry screenshots" --dry-run
    python photo_cleaner.py "old receipts" --backend ollama --model moondream
"""

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

try:
    from flask import Flask, jsonify, render_template_string
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False

# Load secrets from shared Keys folder (supports JSON or .env syntax)
ENV_PATH = "/Users/mike/Documents/Keys/.env"

def load_env_file(path: str) -> None:
    """Load environment variables from JSON or .env file."""
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return
        # Try JSON first
        if content.startswith("{"):
            data = json.loads(content)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(k, str) and isinstance(v, str):
                        os.environ.setdefault(k, v)
                return
        # Fallback to dotenv format
        load_dotenv(path)
    except Exception as e:
        print(f"⚠️  Could not load env file {path}: {e}")

load_env_file(ENV_PATH)

scan_state = {
    "current_photo": None,
    "stats": {"scanned": 0, "matched": 0, "deleted": 0, "cost": 0.0},
    "history": [],
    "is_running": False,
    "stop_requested": False,
    "status": "Idle",
    "meta": {},
}

PRICE_PER_MILLION = {
    "gpt-5.1": 1.25,
    "gpt-5-mini": 0.25,
    "gpt-5-nano": 0.05,
    "gpt-4o": 2.50,
    "gpt-4o-mini": 0.15,
}
TOKENS_PER_IMAGE = 270

# Minimal dashboard template
HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Apple Photos Cleaner</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; padding: 16px; background: #f6f7fb; color: #111; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 12px; padding: 16px; box-shadow: 0 6px 20px rgba(0,0,0,0.06); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px,1fr)); gap: 12px; }
    img { max-width: 100%; max-height: 70vh; object-fit: contain; border-radius: 8px; background: #f8fafc; }
    .log { height: 200px; overflow-y: auto; background: #0f172a; color: #e2e8f0; padding: 8px; border-radius: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; font-size: 13px; }
    .badge { display: inline-block; padding: 4px 8px; border-radius: 12px; background: #e0f2fe; color: #0ea5e9; font-weight: 600; font-size: 12px; }
    .btn { padding: 8px 12px; border-radius: 8px; border: 1px solid #e5e7eb; background: #fff; cursor: pointer; }
  </style>
</head>
<body>
  <header>
    <div>
      <div style="font-size:22px;font-weight:700;">🧹 Apple Photos Cleaner</div>
      <div id="meta" style="color:#475569;font-size:13px;"></div>
    </div>
    <div><span id="status" class="badge">Idle</span></div>
  </header>

  <div class="grid" style="margin-bottom:12px;">
    <div class="card"><div style="color:#475569;font-size:13px;">Scanned</div><div id="scanned" style="font-size:28px;font-weight:700;">0</div></div>
    <div class="card"><div style="color:#475569;font-size:13px;">Matched</div><div id="matched" style="font-size:28px;font-weight:700;color:#f97316;">0</div></div>
    <div class="card"><div style="color:#475569;font-size:13px;">Deleted</div><div id="deleted" style="font-size:28px;font-weight:700;color:#ef4444;">0</div></div>
    <div class="card"><div style="color:#475569;font-size:13px;">Cost (est)</div><div id="cost" style="font-size:28px;font-weight:700;color:#10b981;">$0.00</div></div>
  </div>

  <div class="card" style="margin-bottom:12px;">
    <div style="font-weight:700;margin-bottom:8px;">Current Photo</div>
    <div id="photo-name" style="color:#475569;font-size:14px;margin-bottom:6px;">Waiting...</div>
    <img id="photo-img" src="" alt="" />
    <div id="reason" style="margin-top:8px;font-size:14px;"></div>
    <div id="confidence" style="color:#475569;font-size:13px;"></div>
  </div>

  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <div style="font-weight:700;">Activity</div>
      <button class="btn" onclick="stopScan()">Stop</button>
    </div>
    <div class="log" id="log"></div>
  </div>

<script>
async function fetchStats() {
  try {
    const res = await fetch('/stats');
    const data = await res.json();
    document.getElementById('status').textContent = data.status || 'Idle';
    document.getElementById('scanned').textContent = data.scanned ?? 0;
    document.getElementById('matched').textContent = data.matched ?? 0;
    document.getElementById('deleted').textContent = data.deleted ?? 0;
    document.getElementById('cost').textContent = `$${(data.cost || 0).toFixed(4)}`;
    document.getElementById('meta').textContent = data.meta || '';

    const logEl = document.getElementById('log');
    logEl.innerHTML = (data.history || []).map(l => `<div>${l}</div>`).join('');
    logEl.scrollTop = logEl.scrollHeight;

    if (data.current_photo) {
      document.getElementById('photo-name').textContent = data.current_photo.name || '';
      document.getElementById('reason').textContent = data.current_photo.reason || '';
      const conf = Math.round((data.current_photo.confidence || 0) * 100);
      document.getElementById('confidence').textContent = `Confidence: ${conf}%`;
      document.getElementById('photo-img').src = `data:image/jpeg;base64,${data.current_photo.data}`;
    }
  } catch (e) {
    console.error(e);
  }
}

async function stopScan() {
  await fetch('/stop', { method: 'POST' });
}

setInterval(fetchStats, 1000);
</script>
</body>
</html>
"""

def find_free_port(start: int = 5000, end: int = 5020) -> Optional[int]:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    return None

try:
    import osxphotos
except ImportError:
    print("❌ osxphotos not installed. Run: pip install osxphotos")
    sys.exit(1)

# Optional imports
try:
    import openai
except ImportError:
    openai = None

try:
    import requests
except ImportError:
    requests = None


class PhotoCleaner:
    """Clean Apple Photos based on AI-powered description matching."""

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
            
            # This now loads from the .env file or the environment
            key = openai_api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                print("❌ OPENAI_API_KEY not set in your environment or .env file.")
                print("   Please create a .env file with OPENAI_API_KEY='sk-...'")
                sys.exit(1)
            
            self.client = openai.OpenAI(api_key=key)
        elif backend == "ollama":
            if not requests:
                print("❌ requests library not installed. Run: pip install requests")
                sys.exit(1)
            self.ollama_url = "http://localhost:11434/api/chat"
            # Check if ollama is running
            try:
                requests.get("http://localhost:11434")
            except requests.exceptions.ConnectionError:
                print("❌ Ollama is not running. Run 'ollama serve' in a separate terminal.")
                sys.exit(1)

    def load_photos_library(self) -> int:
        """Load the Apple Photos library."""
        print("📚 Loading Apple Photos library...")
        self.photosdb = osxphotos.PhotosDB()
        total = len(self.photosdb.photos())
        print(f"   Found {total:,} photos in library")
        return total

    def get_photos(
        self,
        limit: Optional[int] = None,
        album: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
    ) -> list:
        """Get photos with optional filters."""
        if not self.photosdb:
            self.load_photos_library()

        photos = self.photosdb.photos()

        # Filter by album
        if album:
            album_photos = set()
            for a in self.photosdb.album_info:
                if album.lower() in a.title.lower():
                    album_photos.update(p.uuid for p in a.photos)
            photos = [p for p in photos if p.uuid in album_photos]

        # Filter by date range
        if from_date:
            photos = [p for p in photos if p.date and p.date >= from_date]
        if to_date:
            photos = [p for p in photos if p.date and p.date <= to_date]

        # Apply limit
        if limit:
            photos = photos[:limit]

        return photos

    def encode_image(self, photo) -> tuple[Optional[str], str]:
        """Export and encode a photo to base64."""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                exported = photo.export(tmpdir, use_photos_export=True)
                if not exported:
                    return None, ""
                
                filepath = Path(exported[0])
                if not filepath.exists():
                    return None, ""

                # Determine media type
                ext = filepath.suffix.lower()
                media_type = {
                    ".png": "image/png",
                    ".gif": "image/gif",
                    ".webp": "image/webp",
                }.get(ext, "image/jpeg")

                # Read and encode
                with open(filepath, "rb") as f:
                    return base64.standard_b64encode(f.read()).decode("utf-8"), media_type
        except Exception as e:
            print(f"   ⚠️  Could not export photo: {e}")
            return None, ""

    def analyze_openai(self, image_data: str, media_type: str, description: str) -> dict:
        """Analyze using OpenAI API."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are an image analysis assistant. Determine if an image matches a user's description for deletion.
Respond with JSON only: {"match": true/false, "confidence": 0.0-1.0, "reason": "brief explanation"}""",
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Does this match: '{description}'?"},
                            {
                                "type": "image_url", 
                                "image_url": {
                                    "url": f"data:{media_type};base64,{image_data}", 
                                    "detail": "low"
                                }
                            },
                        ],
                    },
                ],
                max_tokens=100,
            )
            return self._parse_json_response(response.choices[0].message.content)
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": f"OpenAI error: {str(e)}"}

    def analyze_ollama(self, image_data: str, description: str) -> dict:
        """Analyze using local Ollama instance."""
        try:
            payload = {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {
                        "role": "user",
                        "content": f"Look at this image. Does it match the description '{description}'? Respond with JSON: {{\"match\": true, \"confidence\": 0.9, \"reason\": \"...\"}} or {{\"match\": false, ...}}",
                        "images": [image_data]
                    }
                ]
            }
            response = requests.post(self.ollama_url, json=payload)
            if response.status_code != 200:
                return {"match": False, "confidence": 0, "reason": f"Ollama error: {response.text}"}
            
            return self._parse_json_response(response.json().get("message", {}).get("content", ""))
        except Exception as e:
            return {"match": False, "confidence": 0, "reason": f"Ollama connection error: {str(e)}"}

    def _parse_json_response(self, text: str) -> dict:
        """Helper to clean and parse JSON from LLM response."""
        try:
            text = text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]
            
            return json.loads(text)
        except (json.JSONDecodeError, AttributeError):
            return {"match": False, "confidence": 0, "reason": "Failed to parse JSON response"}

    def analyze_photo(self, photo, description: str) -> dict:
        """Analyze a photo using the selected backend."""
        image_data, media_type = self.encode_image(photo)
        if not image_data:
            return {"match": False, "confidence": 0, "reason": "Could not load image"}

        if self.backend == "openai":
            return self.analyze_openai(image_data, media_type, description)
        else:
            return self.analyze_ollama(image_data, description)

    def delete_photo_via_applescript(self, photo_uuid: str) -> bool:
        """Delete a photo from Apple Photos using AppleScript."""
        # ... implementation same as before but singular ...
        return self.move_to_trash_via_applescript([photo_uuid]) > 0

    def move_to_trash_via_applescript(self, photo_uuids: list[str]) -> int:
        """Move multiple photos to Recently Deleted using AppleScript."""
        if not photo_uuids:
            return 0
        
        uuid_list = '", "'.join(photo_uuids)
        script = f'''
        tell application "Photos"
            set uuidList to {{"{uuid_list}"}}
            set deletedCount to 0
            repeat with uuid in uuidList
                try
                    set targetPhoto to media item id uuid
                    delete targetPhoto
                    set deletedCount to deletedCount + 1
                end try
            end repeat
            return deletedCount
        end tell
        '''
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                try:
                    return int(result.stdout.strip())
                except ValueError:
                    return len(photo_uuids)
            return 0
        except Exception as e:
            print(f"   ⚠️  AppleScript error: {e}")
            return 0

    def run(
        self,
        description: str,
        limit: Optional[int] = None,
        album: Optional[str] = None,
        dry_run: bool = False,
        confirm_each: bool = False,
        batch_size: int = 10,
    ) -> dict:
        """Main entry point."""
        print(f"\n🔍 Looking for photos matching: \"{description}\"")
        print(f"   Backend: {self.backend} ({self.model})")
        
        if dry_run:
            print("   (DRY RUN - no photos will be deleted)\n")

        photos = self.get_photos(limit=limit, album=album)
        print(f"   Scanning {len(photos)} photos...\n")

        stats = {"scanned": 0, "matched": 0, "deleted": 0, "errors": 0}
        to_delete = []

        for i, photo in enumerate(photos):
            stats["scanned"] += 1
            
            if (i + 1) % batch_size == 0:
                print(f"   Progress: {i + 1}/{len(photos)} scanned, {stats['matched']} matches found")

            filename = photo.original_filename or "Unknown"
            result = self.analyze_photo(photo, description)

            if result["match"] and result.get("confidence", 0) >= self.confidence_threshold:
                stats["matched"] += 1
                print(f"\n   ✓ MATCH: {filename}")
                print(f"     Confidence: {result.get('confidence', 0):.0%} - {result.get('reason', 'No reason provided')}")

                if confirm_each and not dry_run:
                    response = input("     Delete this photo? [y/N]: ").strip().lower()
                    if response != "y":
                        print("     Skipped.")
                        continue
                    to_delete.append(photo.uuid)
                elif not confirm_each:
                    to_delete.append(photo.uuid)

        if to_delete:
            if dry_run:
                print(f"\n📋 Would delete {len(to_delete)} photos (dry run)")
            else:
                print(f"\n🗑️  Moving {len(to_delete)} photos to Recently Deleted...")
                stats["deleted"] = self.move_to_trash_via_applescript(to_delete)
                print(f"   ✓ Moved {stats['deleted']} photos to trash")

        print(f"\n{'='*50}")
        print(f"📊 Summary:")
        print(f"   Scanned:  {stats['scanned']}")
        print(f"   Matched:  {stats['matched']}")
        print(f"   Deleted:  {stats['deleted']}")
        print(f"{'='*50}\n")

        return stats


def estimate_cost(model: str) -> float:
    price = PRICE_PER_MILLION.get(model, PRICE_PER_MILLION.get("gpt-5-mini", 0.25))
    return price * TOKENS_PER_IMAGE / 1_000_000


def run_scan_thread(cleaner: "PhotoCleaner", description: str, limit: Optional[int], album: Optional[str], dry_run: bool):
    """Background scan feeding the dashboard."""
    scan_state["is_running"] = True
    scan_state["stop_requested"] = False
    scan_state["stats"] = {"scanned": 0, "matched": 0, "deleted": 0, "cost": 0.0}
    scan_state["history"] = []
    scan_state["status"] = "Loading Photos..."
    scan_state["meta"] = f"{cleaner.backend} • {cleaner.model} • limit={limit or 'all'} • {'dry-run' if dry_run else 'delete'} • desc='{description}'"

    try:
        cleaner.load_photos_library()
        photos = cleaner.get_photos(limit=limit, album=album)
        est_per_image = estimate_cost(cleaner.model) if cleaner.backend == "openai" else 0.0

        for photo in photos:
            if scan_state["stop_requested"]:
                break

            filename = photo.original_filename or "Unknown"
            scan_state["status"] = f"Analyzing {filename}"
            scan_state["stats"]["scanned"] += 1

            img_data, mime = cleaner.encode_image(photo)
            if not img_data:
                continue

            if cleaner.backend == "openai":
                res = cleaner.analyze_openai(img_data, mime, description)
            else:
                res = cleaner.analyze_ollama(img_data, description)

            conf = res.get("confidence", 0)
            is_match = res.get("match", False) and conf >= cleaner.confidence_threshold
            if cleaner.backend == "openai":
                scan_state["stats"]["cost"] += est_per_image

            scan_state["current_photo"] = {
                "name": filename,
                "data": img_data,
                "reason": res.get("reason", ""),
                "confidence": conf,
                "is_match": is_match,
            }

            if is_match:
                scan_state["stats"]["matched"] += 1
                scan_state["history"].append(f"MATCH {filename} ({conf:.0%})")
                if not dry_run:
                    cleaner.move_to_trash_via_applescript([photo.uuid])
                    scan_state["stats"]["deleted"] += 1
                    scan_state["history"].append(f"DELETED {filename}")
            time.sleep(0.05)

    except Exception as e:
        scan_state["history"].append(f"ERROR: {e}")
    finally:
        scan_state["status"] = "Complete" if not scan_state.get("stop_requested") else "Stopped"
        scan_state["is_running"] = False


def start_dashboard(cleaner: "PhotoCleaner", description: str, limit: Optional[int], album: Optional[str], dry_run: bool):
    if not FLASK_AVAILABLE:
        print("❌ Flask is required for the dashboard. Install with: pip install flask")
        sys.exit(1)

    app = Flask(__name__)

    @app.route("/")
    def home():
        return render_template_string(HTML_TEMPLATE)

    @app.route("/stats")
    def stats():
        s = scan_state
        return jsonify({
            "status": s.get("status", "Idle"),
            "scanned": s["stats"].get("scanned", 0),
            "matched": s["stats"].get("matched", 0),
            "deleted": s["stats"].get("deleted", 0),
            "cost": s["stats"].get("cost", 0.0),
            "current_photo": s.get("current_photo"),
            "history": s.get("history", [])[-200:],
            "meta": s.get("meta", ""),
        })

    @app.route("/stop", methods=["POST"])
    def stop():
        scan_state["stop_requested"] = True
        return jsonify({"status": "stopping"})

    t = threading.Thread(target=run_scan_thread, args=(cleaner, description, limit, album, dry_run), daemon=True)
    t.start()

    port = find_free_port()
    if not port:
        print("❌ No free port found (5000-5020).")
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"\n🚀 Starting Web UI at {url}")
    webbrowser.open(url)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

def run_interactive():
    """Interactive prompts with sensible defaults."""
    print("\n" + "=" * 50)
    print("  🧹 Apple Photos Cleaner (Interactive)")
    print("=" * 50)

    # 1) Backend (default OpenAI)
    be_input = input("\n1. Backend: 1) OpenAI (default)  2) Ollama [1]: ").strip()
    backend = "ollama" if be_input == "2" else "openai"

    # 2) Model selection (default gpt-5-mini)
    model_options = [
        ("gpt-5.1", "Highest accuracy"),
        ("gpt-5-mini", "Best balance (default)"),
        ("gpt-5-nano", "Fastest & cheapest"),
        ("gpt-4o", "Legacy strong multimodal"),
        ("gpt-4o-mini", "Legacy budget"),
    ] if backend == "openai" else [
        ("moondream", "Local fast (default)"),
        ("llava", "Local detailed"),
    ]
    print("\n2. Model choices:")
    for idx, (name, desc) in enumerate(model_options, 1):
        print(f"   {idx}. {name:<12} ({desc})")
    model_choice = input("   Select [Enter for default]: ").strip()
    if model_choice and model_choice.isdigit():
        idx = int(model_choice) - 1
        model = model_options[idx][0] if 0 <= idx < len(model_options) else model_options[1][0]
    else:
        model = "gpt-5-mini" if backend == "openai" else model_options[0][0]

    # 3) Description
    description = ""
    while not description:
        description = input("\n3. Describe photos to delete (e.g., 'bank statements'): ").strip()

    # 4) Limit (default 50; enter 'all' or blank for no limit)
    lim_raw = input("4. Limit photos to scan [50 | all]: ").strip().lower()
    if lim_raw in ("", "all"):
        limit = None  # no limit
    elif lim_raw.isdigit():
        limit = int(lim_raw)
    else:
        print("   Invalid limit, defaulting to 50")
        limit = 50

    # 5) Visual Dashboard (default Y)
    vis_raw = input("5. Visual Dashboard? (Y/n) [Y]: ").strip().lower()
    visual = vis_raw != "n"

    # 6) Dry run (default Y)
    dr_raw = input("6. Dry run (don't delete)? (Y/n) [Y]: ").strip().lower()
    dry_run = dr_raw != "n"

    cleaner = PhotoCleaner(
        backend=backend,
        model=model,
        confidence_threshold=0.7,
    )

    if visual:
        start_dashboard(cleaner, description, limit, None, dry_run)
    else:
        print("\n🚀 Starting CLI scan...")
        cleaner.run(
            description=description,
            limit=limit,
            dry_run=dry_run,
        )


def main():
    parser = argparse.ArgumentParser(description="Delete photos matching a description (defaults to interactive mode)")
    parser.add_argument("description", nargs="?", help="Description of photos to delete (omit for interactive mode)")
    parser.add_argument("--limit", type=int, help="Max photos to scan")
    parser.add_argument("--album", help="Only scan this album")
    parser.add_argument("--dry-run", action="store_true", help="Don't delete anything")
    parser.add_argument("--confirm-each", action="store_true", help="Ask before each delete")
    parser.add_argument("--threshold", type=float, default=0.7, help="Confidence threshold")
    
    # Backend arguments
    parser.add_argument("--backend", choices=["openai", "ollama"], default="openai", help="AI backend to use")
    parser.add_argument("--model", nargs='?', const='SELECT_MODE', help="Model name (default: gpt-5-mini for openai, moondream for ollama)")

    args = parser.parse_args()

    # If no description provided, run interactive mode
    if not args.description:
        run_interactive()
        return

    # Handle model selection if --model is used without value
    if args.model == 'SELECT_MODE':
        print("\n🤖 Select a Vision Model:")
        print("   1. gpt-5.1        (Highest accuracy)")
        print("   2. gpt-5-mini     (Best balance - default)")
        print("   3. gpt-5-nano     (Fastest & cheapest)")
        print("   4. gpt-4o         (Legacy strong multimodal)")
        print("   5. gpt-4o-mini    (Legacy budget)")
        print("   6. moondream      (Local/Free via Ollama)")
        print("   7. Custom...      (Enter your own model name)")
        
        choice = input("\n   Choose model [1-7]: ").strip()
        if choice == '1':
            args.model = "gpt-5.1"
            args.backend = "openai"
        elif choice == '2':
            args.model = "gpt-5-mini"
            args.backend = "openai"
        elif choice == '3':
            args.model = "gpt-5-nano"
            args.backend = "openai"
        elif choice == '4':
            args.model = "gpt-4o"
            args.backend = "openai"
        elif choice == '5':
            args.model = "gpt-4o-mini"
            args.backend = "openai"
        elif choice == '6':
            args.model = "moondream"
            args.backend = "ollama"
        elif choice == '7':
            args.model = input("   Enter model name: ").strip()
            # Guess backend based on common names, default to openai
            if "llama" in args.model or "dream" in args.model:
                args.backend = "ollama"
            else:
                args.backend = "openai"
        else:
            print("   Invalid choice, defaulting to gpt-5-mini")
            args.model = "gpt-5-mini"
            args.backend = "openai"

    # Default models
    if not args.model:
        args.model = "gpt-5-mini" if args.backend == "openai" else "moondream"

    # Check API key for OpenAI
    # if args.backend == "openai" and not os.getenv("OPENAI_API_KEY"):
    #    print("❌ OPENAI_API_KEY not set for OpenAI backend")
    #    sys.exit(1)

    cleaner = PhotoCleaner(
        backend=args.backend,
        model=args.model,
        confidence_threshold=args.threshold,
    )

    cleaner.run(
        description=args.description,
        limit=args.limit,
        album=args.album,
        dry_run=args.dry_run,
        confirm_each=args.confirm_each,
    )


if __name__ == "__main__":
    main()
