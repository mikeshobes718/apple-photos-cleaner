#!/usr/bin/env python3
"""
Apple Photos Cleaner - Simple & Reliable
Focuses on accuracy over speed. Processes one photo at a time.
Includes optional web dashboard for visual monitoring.
"""

import os
import sys
import json
import time
import base64
import subprocess
import threading
from pathlib import Path
from io import BytesIO
from datetime import datetime

# ============================================================
# Configuration
# ============================================================
ENV_PATH = os.path.expanduser("~/Documents/Keys/.env")
LOG_PATH = os.path.expanduser("~/Documents/logs/photo_cleaner.log")
ALBUM_NAME = "🤖 AI Matches - To Delete"
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7
DASHBOARD_PORT = 5050

# File types to skip (videos, RAW files)
SKIP_EXTENSIONS = {'.mov', '.mp4', '.m4v', '.avi', '.3gp', '.mkv', '.webm',
                   '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2'}

# Global state for dashboard
state = {
    "running": False,
    "stopping": False,
    "description": "",
    "current_photo": "",
    "current_index": 0,
    "total": 0,
    "scanned": 0,
    "matched": 0,
    "added": 0,
    "skipped": 0,
    "errors": 0,
    "cost": 0.0,
    "rate": 0.0,
    "eta_min": 0,
    "matches": [],
    "status": "idle",
    "log": []
}

# ============================================================
# Setup
# ============================================================
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Suppress noisy logging
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def log(message: str):
    """Log message to file and print."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def load_api_key():
    """Load OpenAI API key from JSON file."""
    if not os.path.exists(ENV_PATH):
        print(f"\n❌ API key file not found: {ENV_PATH}")
        print(f"   Create it with: {'{'}\"OPENAI_API_KEY\": \"sk-...\"{'}'}")
        sys.exit(1)
    
    with open(ENV_PATH) as f:
        data = json.load(f)
    
    key = data.get("OPENAI_API_KEY")
    if not key:
        print(f"\n❌ OPENAI_API_KEY not found in {ENV_PATH}")
        sys.exit(1)
    
    os.environ["OPENAI_API_KEY"] = key
    return key

# ============================================================
# Dependencies
# ============================================================
def check_dependencies():
    """Check and import required packages."""
    missing = []
    
    try:
        import openai
    except ImportError:
        missing.append("openai")
    
    try:
        import osxphotos
    except ImportError:
        missing.append("osxphotos")
    
    try:
        from PIL import Image
    except ImportError:
        missing.append("pillow")
    
    try:
        from pillow_heif import register_heif_opener
    except ImportError:
        missing.append("pillow-heif")
    
    if missing:
        print(f"\n❌ Missing packages: {', '.join(missing)}")
        print(f"   Run: pip install {' '.join(missing)}")
        sys.exit(1)
    
    # Register HEIC support
    from pillow_heif import register_heif_opener
    register_heif_opener()

# ============================================================
# Photo Processing
# ============================================================
def encode_photo(photo) -> tuple:
    """
    Encode photo to base64 for API.
    Returns: (base64_data, error_message)
    """
    from PIL import Image
    
    filename = photo.original_filename or "unknown"
    filepath = Path(photo.path)
    
    # Check extension
    ext = filepath.suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return None, f"Skipped (video/RAW): {filename}"
    
    try:
        # Read and resize image
        with Image.open(filepath) as img:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'P', 'LA'):
                img = img.convert('RGB')
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Resize to max dimension
            img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE), Image.Resampling.LANCZOS)
            
            # Save to buffer
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            
            return base64.b64encode(buffer.read()).decode('utf-8'), None
            
    except Exception as e:
        return None, f"Error encoding {filename}: {str(e)[:50]}"

def analyze_photo(client, image_data: str, description: str) -> dict:
    """
    Analyze photo with OpenAI Vision API.
    Returns: {"match": bool, "confidence": float, "reason": str}
    """
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": """You analyze images to determine if they match a description.
Respond ONLY with valid JSON in this exact format:
{"match": true, "confidence": 0.85, "reason": "Brief explanation"}

- match: true if image matches the description, false otherwise
- confidence: 0.0 to 1.0 (how confident you are)
- reason: Brief explanation (max 100 chars)"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Does this image match: \"{description}\"?"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                                "detail": "low"
                            }
                        }
                    ]
                }
            ],
            max_tokens=200,
            timeout=45
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        # Handle markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        
        result = json.loads(content)
        return {
            "match": bool(result.get("match", False)),
            "confidence": float(result.get("confidence", 0)),
            "reason": str(result.get("reason", ""))[:100]
        }
        
    except json.JSONDecodeError as e:
        return {"match": False, "confidence": 0, "reason": f"JSON parse error: {str(e)[:30]}"}
    except Exception as e:
        return {"match": False, "confidence": 0, "reason": f"API error: {str(e)[:50]}"}

def ensure_album_exists() -> bool:
    """Create the album if it doesn't exist."""
    script = f'''
    tell application "Photos"
        try
            album "{ALBUM_NAME}"
        on error
            make new album named "{ALBUM_NAME}"
        end try
        return "ok"
    end tell
    '''
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
        return True
    except:
        return False

def add_to_album(photo_uuid: str, retries: int = 2) -> bool:
    """Add photo to the matches album using AppleScript."""
    # Simpler script that's faster
    script = f'''
    tell application "Photos"
        try
            set thePhoto to media item id "{photo_uuid}"
            add {{thePhoto}} to album "{ALBUM_NAME}"
            return "ok"
        on error
            return "fail"
        end try
    end tell
    '''
    
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=15  # Shorter timeout
            )
            if "ok" in result.stdout:
                return True
        except subprocess.TimeoutExpired:
            if attempt < retries:
                time.sleep(1)  # Brief pause before retry
                continue
            log("WARNING: AppleScript timed out adding to album")
            return False
        except Exception as e:
            log(f"WARNING: AppleScript error: {e}")
            return False
    
    return False

def add_photos_batch(uuids: list) -> int:
    """Add multiple photos to album in one AppleScript call."""
    if not uuids:
        return 0
    
    # Create album first
    ensure_album_exists()
    
    # Build list of photo references
    photo_refs = ", ".join([f'media item id "{u}"' for u in uuids[:50]])  # Max 50 at a time
    
    script = f'''
    tell application "Photos"
        set addedCount to 0
        try
            set photoList to {{{photo_refs}}}
            add photoList to album "{ALBUM_NAME}"
            set addedCount to (count of photoList)
        end try
        return addedCount
    end tell
    '''
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=60
        )
        try:
            return int(result.stdout.strip())
        except:
            return 0
    except:
        return 0

# ============================================================
# Dashboard HTML
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Photo Cleaner Pro</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-gradient: linear-gradient(135deg, #0c0c0c 0%, #1a1a2e 50%, #16213e 100%);
            --card-bg: rgba(255, 255, 255, 0.03);
            --card-border: rgba(255, 255, 255, 0.08);
            --card-glow: rgba(99, 102, 241, 0.1);
            --text-primary: #f8fafc;
            --text-secondary: rgba(248, 250, 252, 0.6);
            --text-muted: rgba(248, 250, 252, 0.4);
            --accent: #818cf8;
            --accent-glow: rgba(129, 140, 248, 0.4);
            --success: #34d399;
            --success-bg: rgba(52, 211, 153, 0.15);
            --danger: #f87171;
            --danger-bg: rgba(248, 113, 113, 0.15);
            --warning: #fbbf24;
        }
        
        .light {
            --bg-gradient: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 50%, #cbd5e1 100%);
            --card-bg: rgba(255, 255, 255, 0.7);
            --card-border: rgba(0, 0, 0, 0.08);
            --card-glow: rgba(99, 102, 241, 0.08);
            --text-primary: #0f172a;
            --text-secondary: rgba(15, 23, 42, 0.7);
            --text-muted: rgba(15, 23, 42, 0.5);
            --accent: #6366f1;
            --accent-glow: rgba(99, 102, 241, 0.3);
        }
        
        * { box-sizing: border-box; margin: 0; padding: 0; }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-gradient);
            background-attachment: fixed;
            color: var(--text-primary);
            min-height: 100vh;
            padding: 2rem;
            line-height: 1.6;
        }
        
        .container {
            max-width: 1100px;
            margin: 0 auto;
        }
        
        /* Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--card-border);
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        
        .logo-icon {
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, var(--accent), #a78bfa);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            box-shadow: 0 8px 32px var(--accent-glow);
        }
        
        .logo-text h1 {
            font-size: 1.5rem;
            font-weight: 700;
            letter-spacing: -0.02em;
        }
        
        .logo-text span {
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }
        
        .theme-toggle {
            width: 44px;
            height: 44px;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            color: var(--text-primary);
            cursor: pointer;
            font-size: 1.2rem;
            transition: all 0.3s ease;
            backdrop-filter: blur(10px);
        }
        
        .theme-toggle:hover {
            background: var(--card-glow);
            transform: scale(1.05);
        }
        
        /* Cards */
        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 20px;
            padding: 1.75rem;
            margin-bottom: 1.5rem;
            backdrop-filter: blur(20px);
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
        }
        
        /* Status Section */
        .status-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.25rem;
        }
        
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: var(--text-muted);
        }
        
        .status-dot.running {
            background: var(--success);
            box-shadow: 0 0 12px var(--success);
            animation: pulse 2s infinite;
        }
        
        .status-dot.complete {
            background: var(--accent);
            box-shadow: 0 0 12px var(--accent);
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.7; transform: scale(1.1); }
        }
        
        .status-text {
            font-size: 1.1rem;
            font-weight: 500;
        }
        
        .status-badge {
            font-size: 0.75rem;
            padding: 0.35rem 0.75rem;
            border-radius: 20px;
            background: var(--card-glow);
            color: var(--text-secondary);
            font-weight: 500;
        }
        
        .current-file {
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-bottom: 1.25rem;
            font-family: 'SF Mono', Monaco, monospace;
            padding: 0.5rem 0.75rem;
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        /* Progress Bar */
        .progress-container {
            margin-bottom: 1.75rem;
        }
        
        .progress-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.5rem;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }
        
        .progress-bar {
            height: 8px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent), #a78bfa);
            border-radius: 10px;
            transition: width 0.4s ease;
            box-shadow: 0 0 20px var(--accent-glow);
        }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        @media (max-width: 768px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
        }
        
        .stat-card {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.25rem;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .stat-card:hover {
            background: var(--card-glow);
            transform: translateY(-2px);
        }
        
        .stat-card.highlight {
            background: var(--success-bg);
            border-color: rgba(52, 211, 153, 0.3);
        }
        
        .stat-icon {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
        }
        
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            background: linear-gradient(135deg, var(--text-primary), var(--accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .stat-card.highlight .stat-value {
            background: linear-gradient(135deg, var(--success), #6ee7b7);
            -webkit-background-clip: text;
            background-clip: text;
        }
        
        .stat-label {
            font-size: 0.75rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 0.25rem;
        }
        
        /* Action Buttons */
        .actions {
            display: flex;
            gap: 1rem;
        }
        
        .btn {
            flex: 1;
            padding: 1rem 1.5rem;
            border: none;
            border-radius: 14px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            transition: all 0.3s ease;
            font-family: inherit;
        }
        
        .btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            transform: none !important;
        }
        
        .btn-danger {
            background: var(--danger-bg);
            color: var(--danger);
            border: 1px solid rgba(248, 113, 113, 0.3);
        }
        
        .btn-danger:hover:not(:disabled) {
            background: var(--danger);
            color: white;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(248, 113, 113, 0.3);
        }
        
        .btn-success {
            background: var(--success-bg);
            color: var(--success);
            border: 1px solid rgba(52, 211, 153, 0.3);
        }
        
        .btn-success:hover:not(:disabled) {
            background: var(--success);
            color: white;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(52, 211, 153, 0.3);
        }
        
        /* Matches Section */
        .matches-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1.25rem;
        }
        
        .matches-title {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            font-size: 1.1rem;
            font-weight: 600;
        }
        
        .matches-count {
            background: var(--accent);
            color: white;
            font-size: 0.75rem;
            padding: 0.25rem 0.6rem;
            border-radius: 20px;
            font-weight: 600;
        }
        
        .matches-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 1rem;
        }
        
        .match-card {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            overflow: hidden;
            transition: all 0.3s ease;
        }
        
        .match-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.2);
            border-color: var(--accent);
        }
        
        .match-image {
            width: 100%;
            height: 160px;
            object-fit: cover;
            background: rgba(0, 0, 0, 0.3);
        }
        
        .match-info {
            padding: 1rem;
        }
        
        .match-name {
            font-weight: 600;
            font-size: 0.9rem;
            margin-bottom: 0.35rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .match-reason {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-bottom: 0.75rem;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        
        .match-footer {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .confidence-badge {
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.3rem 0.6rem;
            border-radius: 8px;
            background: var(--success-bg);
            color: var(--success);
        }
        
        .confidence-badge.high { background: var(--success-bg); color: var(--success); }
        .confidence-badge.medium { background: rgba(251, 191, 36, 0.15); color: var(--warning); }
        
        .match-type {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        
        .no-matches {
            text-align: center;
            padding: 3rem;
            color: var(--text-muted);
        }
        
        .no-matches-icon {
            font-size: 3rem;
            margin-bottom: 1rem;
            opacity: 0.5;
        }
        
        /* Description display */
        .search-query {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.75rem 1rem;
            background: rgba(129, 140, 248, 0.1);
            border: 1px solid rgba(129, 140, 248, 0.2);
            border-radius: 12px;
            margin-bottom: 1.5rem;
            font-size: 0.9rem;
        }
        
        .search-query-label {
            color: var(--text-muted);
        }
        
        .search-query-text {
            color: var(--accent);
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">
                <div class="logo-icon">🧹</div>
                <div class="logo-text">
                    <span>AI Powered</span>
                    <h1>Photo Cleaner</h1>
                </div>
            </div>
            <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
                <span id="themeIcon">🌙</span>
            </button>
        </header>
        
        <div class="card">
            <div class="status-header">
                <div class="status-indicator">
                    <div class="status-dot" id="statusDot"></div>
                    <span class="status-text" id="statusText">Initializing...</span>
                </div>
                <span class="status-badge" id="modeBadge">SCANNING</span>
            </div>
            
            <div class="search-query" id="searchQuery" style="display: none;">
                <span class="search-query-label">Looking for:</span>
                <span class="search-query-text" id="searchText"></span>
            </div>
            
            <div class="current-file" id="currentFile">Waiting...</div>
            
            <div class="progress-container">
                <div class="progress-header">
                    <span id="progressText">0 of 0 photos</span>
                    <span id="progressPct">0%</span>
                </div>
                <div class="progress-bar">
                    <div class="progress-fill" id="progressFill" style="width: 0%"></div>
                </div>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon">📷</div>
                    <div class="stat-value" id="scanned">0</div>
                    <div class="stat-label">Scanned</div>
                </div>
                <div class="stat-card highlight">
                    <div class="stat-icon">✨</div>
                    <div class="stat-value" id="matched">0</div>
                    <div class="stat-label">Matches</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📁</div>
                    <div class="stat-value" id="added">0</div>
                    <div class="stat-label">Added to Album</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">⚡</div>
                    <div class="stat-value" id="rate">0.0</div>
                    <div class="stat-label">Photos/sec</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">⏱️</div>
                    <div class="stat-value" id="eta">--</div>
                    <div class="stat-label">ETA (min)</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">💰</div>
                    <div class="stat-value" id="cost">$0.00</div>
                    <div class="stat-label">API Cost</div>
                </div>
            </div>
            
            <div class="actions">
                <button class="btn btn-danger" id="stopBtn" onclick="stopScan()">
                    <span>⏹</span> Stop Scan
                </button>
                <button class="btn btn-success" id="albumBtn" onclick="openAlbum()">
                    <span>📂</span> Open Album
                </button>
            </div>
        </div>
        
        <div class="card">
            <div class="matches-header">
                <div class="matches-title">
                    <span>🎯</span>
                    <span>Matched Photos</span>
                    <span class="matches-count" id="matchCount">0</span>
                </div>
            </div>
            
            <div class="matches-grid" id="matchesGrid">
                <div class="no-matches">
                    <div class="no-matches-icon">🔍</div>
                    <div>No matches found yet</div>
                    <div style="font-size: 0.85rem; margin-top: 0.5rem;">Matches will appear here as photos are scanned</div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        // Theme based on time
        const hour = new Date().getHours();
        let isDark = !(hour >= 7 && hour < 19);
        if (!isDark) document.body.classList.add('light');
        updateThemeIcon();
        
        function toggleTheme() {
            isDark = !isDark;
            document.body.classList.toggle('light');
            updateThemeIcon();
        }
        
        function updateThemeIcon() {
            document.getElementById('themeIcon').textContent = isDark ? '☀️' : '🌙';
        }
        
        function update() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    // Status
                    const dot = document.getElementById('statusDot');
                    const text = document.getElementById('statusText');
                    const badge = document.getElementById('modeBadge');
                    
                    text.textContent = data.status;
                    dot.className = 'status-dot ' + (data.running ? 'running' : (data.status.includes('COMPLETE') ? 'complete' : ''));
                    badge.textContent = data.running ? 'SCANNING' : (data.status.includes('COMPLETE') ? 'COMPLETE' : 'IDLE');
                    
                    // Search query
                    if (data.description) {
                        document.getElementById('searchQuery').style.display = 'flex';
                        document.getElementById('searchText').textContent = data.description;
                    }
                    
                    // Current file
                    document.getElementById('currentFile').textContent = data.current_photo || 'Waiting...';
                    
                    // Progress
                    const pct = data.total > 0 ? (data.current_index / data.total * 100) : 0;
                    document.getElementById('progressFill').style.width = pct + '%';
                    document.getElementById('progressText').textContent = `${data.current_index.toLocaleString()} of ${data.total.toLocaleString()} photos`;
                    document.getElementById('progressPct').textContent = pct.toFixed(1) + '%';
                    
                    // Stats
                    document.getElementById('scanned').textContent = data.scanned.toLocaleString();
                    document.getElementById('matched').textContent = data.matched.toLocaleString();
                    document.getElementById('added').textContent = data.added.toLocaleString();
                    document.getElementById('rate').textContent = data.rate.toFixed(1);
                    document.getElementById('eta').textContent = data.eta_min > 0 ? data.eta_min : '--';
                    document.getElementById('cost').textContent = '$' + data.cost.toFixed(4);
                    
                    // Match count badge
                    document.getElementById('matchCount').textContent = data.matched;
                    
                    // Matches grid
                    const grid = document.getElementById('matchesGrid');
                    if (data.matches && data.matches.length > 0) {
                        grid.innerHTML = data.matches.map(m => {
                            const conf = Math.round(m.confidence * 100);
                            const confClass = conf >= 85 ? 'high' : 'medium';
                            return `
                                <div class="match-card">
                                    <img class="match-image" 
                                         src="/api/thumb/${encodeURIComponent(m.path || '')}" 
                                         onerror="this.style.background='linear-gradient(135deg,#1a1a2e,#16213e)';this.style.display='flex'" 
                                         alt="${m.filename}" />
                                    <div class="match-info">
                                        <div class="match-name">${m.filename}</div>
                                        <div class="match-reason">${m.reason}</div>
                                        <div class="match-footer">
                                            <span class="confidence-badge ${confClass}">${conf}% match</span>
                                            <span class="match-type">AI Detected</span>
                                        </div>
                                    </div>
                                </div>
                            `;
                        }).join('');
                    }
                    
                    // Buttons
                    document.getElementById('stopBtn').disabled = !data.running;
                    document.getElementById('albumBtn').disabled = data.matched === 0;
                })
                .catch(e => console.error('Update error:', e));
        }
        
        function stopScan() {
            fetch('/api/stop', { method: 'POST' });
        }
        
        function openAlbum() {
            fetch('/api/open-album', { method: 'POST' });
        }
        
        setInterval(update, 500);
        update();
    </script>
</body>
</html>
"""

# ============================================================
# Dashboard Server
# ============================================================
def start_dashboard():
    """Start Flask dashboard server."""
    try:
        from flask import Flask, jsonify
    except ImportError:
        print("⚠️  Flask not installed. Run: pip install flask")
        return None
    
    app = Flask(__name__)
    app.logger.setLevel("WARNING")
    
    import logging
    log_flask = logging.getLogger('werkzeug')
    log_flask.setLevel(logging.WARNING)
    
    @app.route('/')
    def index():
        return DASHBOARD_HTML
    
    @app.route('/api/status')
    def api_status():
        return jsonify(state)
    
    @app.route('/api/stop', methods=['POST'])
    def api_stop():
        state["stopping"] = True
        return jsonify({"ok": True})
    
    @app.route('/api/open-album', methods=['POST'])
    def api_open_album():
        subprocess.run(["open", "-a", "Photos"], capture_output=True)
        return jsonify({"ok": True})
    
    @app.route('/api/thumb/<path:filepath>')
    def api_thumb(filepath):
        """Serve photo thumbnail."""
        from flask import send_file
        from PIL import Image
        
        try:
            # Decode the path
            import urllib.parse
            filepath = urllib.parse.unquote(filepath)
            
            if not os.path.exists(filepath):
                return "", 404
            
            # Create thumbnail
            with Image.open(filepath) as img:
                if img.mode in ('RGBA', 'P', 'LA'):
                    img = img.convert('RGB')
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                img.thumbnail((200, 200), Image.Resampling.LANCZOS)
                
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=80)
                buffer.seek(0)
                
                return send_file(buffer, mimetype='image/jpeg')
        except Exception as e:
            return "", 404
    
    # Run in background thread
    def run():
        app.run(host='127.0.0.1', port=DASHBOARD_PORT, debug=False, use_reloader=False)
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    
    return thread

# ============================================================
# Main Scanner
# ============================================================
def scan_photos(description: str, limit: int = None, dry_run: bool = False, use_dashboard: bool = False):
    """
    Main scanning function.
    
    Args:
        description: What to look for in photos
        limit: Max photos to scan (None = all)
        dry_run: If True, don't add matches to album
        use_dashboard: If True, launch web dashboard
    """
    import openai
    import osxphotos
    
    # Reset state
    state.update({
        "running": True,
        "stopping": False,
        "description": description,
        "current_photo": "",
        "current_index": 0,
        "total": 0,
        "scanned": 0,
        "matched": 0,
        "added": 0,
        "skipped": 0,
        "errors": 0,
        "cost": 0.0,
        "rate": 0.0,
        "eta_min": 0,
        "matches": [],
        "status": "Starting...",
        "log": []
    })
    
    # Start dashboard if requested
    if use_dashboard:
        start_dashboard()
        import webbrowser
        time.sleep(0.5)
        webbrowser.open(f"http://127.0.0.1:{DASHBOARD_PORT}")
        print(f"\n🌐 Dashboard: http://127.0.0.1:{DASHBOARD_PORT}")
    
    log(f"=" * 60)
    log(f"Starting scan: \"{description}\"")
    log(f"Mode: {'DRY RUN' if dry_run else 'LIVE'} | Limit: {limit or 'all'}")
    log(f"=" * 60)
    
    # Initialize OpenAI client
    client = openai.OpenAI()
    
    # Load Photos library
    print("\n📚 Loading Photos library...")
    start_time = time.time()
    
    try:
        db = osxphotos.PhotosDB()
    except Exception as e:
        log(f"ERROR: Failed to load Photos library: {e}")
        print("\n❌ Failed to load Photos library.")
        print("   Try restarting the Photos app and run again.")
        return
    
    all_photos = list(db.photos())
    load_time = time.time() - start_time
    
    print(f"   ✓ Loaded {len(all_photos):,} photos in {load_time:.1f} seconds")
    state["status"] = "Filtering local photos..."
    
    # Filter for locally available photos (not just in iCloud)
    print("   Checking for locally available photos...")
    photos = []
    for p in all_photos:
        if p.path and Path(p.path).exists():
            photos.append(p)
    
    print(f"   ✓ Found {len(photos):,} locally available photos")
    print(f"   ℹ️  {len(all_photos) - len(photos):,} photos are in iCloud only")
    
    if len(photos) == 0:
        print("\n⚠️  No locally available photos found!")
        print("   Your photos are stored in iCloud.")
        print("   To scan them, open Photos app and download some photos first.")
        print("   (Select photos → File → Download Original)")
        state["running"] = False
        state["status"] = "No local photos found"
        return
    
    log(f"Library loaded: {len(all_photos):,} total, {len(photos):,} local in {load_time:.1f}s")
    
    # Apply limit
    if limit:
        photos = photos[:limit]
    
    total = len(photos)
    state["total"] = total
    state["status"] = f"Scanning {total:,} photos..."
    print(f"\n🔍 Scanning {total:,} photos...\n")
    
    # Statistics
    stats = {
        "scanned": 0,
        "matched": 0,
        "added_to_album": 0,
        "skipped": 0,
        "errors": 0,
        "cost": 0.0
    }
    
    # Cost estimate (gpt-4o-mini: ~$0.00015 per image with low detail)
    cost_per_image = 0.00015
    
    matches = []
    scan_start = time.time()
    
    # Process each photo
    for i, photo in enumerate(photos, 1):
        # Check for stop request
        if state["stopping"]:
            print("\n⏹️  Scan stopped by user")
            break
        
        filename = photo.original_filename or f"photo_{i}"
        state["current_index"] = i
        state["current_photo"] = filename
        
        # Progress indicator
        progress = f"[{i}/{total}]"
        
        # Encode photo
        image_data, error = encode_photo(photo)
        
        if error:
            if "Skipped" in error:
                stats["skipped"] += 1
                state["skipped"] = stats["skipped"]
            else:
                stats["errors"] += 1
                state["errors"] = stats["errors"]
                log(f"ERROR: {error}")
            print(f"   {progress} ⏭️  {filename[:40]}")
            continue
        
        # Analyze with AI
        result = analyze_photo(client, image_data, description)
        stats["scanned"] += 1
        stats["cost"] += cost_per_image
        state["scanned"] = stats["scanned"]
        state["cost"] = stats["cost"]
        
        is_match = result["match"] and result["confidence"] >= CONFIDENCE_THRESHOLD
        
        if is_match:
            stats["matched"] += 1
            state["matched"] = stats["matched"]
            match_data = {
                "uuid": photo.uuid,
                "filename": filename,
                "path": str(photo.path) if photo.path else "",
                "confidence": result["confidence"],
                "reason": result["reason"]
            }
            matches.append(match_data)
            state["matches"] = matches[-20:]  # Keep last 20 for UI
            
            print(f"   {progress} ⚡ MATCH: {filename[:35]} ({result['confidence']:.0%})")
            print(f"           └─ {result['reason']}")
            log(f"MATCH: {filename} | conf={result['confidence']:.2f} | {result['reason']}")
        else:
            print(f"   {progress} ○  {filename[:40]}")
        
        # Update rate and ETA
        elapsed = time.time() - scan_start
        if elapsed > 0 and stats["scanned"] > 0:
            rate = stats["scanned"] / elapsed
            remaining = (total - i) / rate if rate > 0 else 0
            state["rate"] = rate
            state["eta_min"] = int(remaining / 60)
        
        # Show progress every 10 photos
        if i % 10 == 0:
            print(f"\n   📊 Progress: {i}/{total} | Rate: {state['rate']:.1f}/sec | ETA: {state['eta_min']} min\n")
    
    # Add matches to album at the end using batch add
    if matches and not dry_run:
        print(f"\n📁 Adding {len(matches)} matches to album...")
        state["status"] = f"Adding {len(matches)} matches to album..."
        
        # Try batch add first (faster)
        uuids = [m["uuid"] for m in matches]
        added_count = add_photos_batch(uuids)
        
        if added_count > 0:
            print(f"   ✓ Batch added {added_count} photos")
            state["added"] = added_count
        else:
            # Fall back to one-by-one
            print(f"   Batch failed, trying one-by-one...")
            ensure_album_exists()
            for m in matches:
                if add_to_album(m["uuid"]):
                    added_count += 1
                    state["added"] = added_count
                    print(f"   ✓ Added {m['filename'][:40]}")
                else:
                    print(f"   ⚠ Failed: {m['filename'][:40]}")
        
        stats["added_to_album"] = added_count
    
    # Summary
    state["running"] = False
    state["status"] = f"COMPLETE - {stats['matched']} matches found"
    
    print(f"\n{'=' * 60}")
    print(f"📊 SCAN COMPLETE")
    print(f"{'=' * 60}")
    print(f"   Photos scanned:  {stats['scanned']:,}")
    print(f"   Matches found:   {stats['matched']:,}")
    print(f"   Added to album:  {stats['added_to_album']:,}")
    print(f"   Skipped:         {stats['skipped']:,} (videos/RAW)")
    print(f"   Errors:          {stats['errors']:,}")
    print(f"   Estimated cost:  ${stats['cost']:.4f}")
    print(f"{'=' * 60}")
    
    log(f"COMPLETE: scanned={stats['scanned']} matched={stats['matched']} added={stats['added_to_album']} cost=${stats['cost']:.4f}")
    
    # Show matches summary
    if matches:
        print(f"\n📋 Matches ({len(matches)}):")
        for m in matches[:20]:  # Show first 20
            print(f"   • {m['filename'][:50]} ({m['confidence']:.0%})")
        if len(matches) > 20:
            print(f"   ... and {len(matches) - 20} more")
        
        if not dry_run and stats["added_to_album"] > 0:
            print(f"\n📁 Review matches in Photos app:")
            print(f"   Albums → \"{ALBUM_NAME}\"")
            print(f"   Select all (⌘A) → Delete (⌫)")
            
            # Open Photos app
            subprocess.run(["open", "-a", "Photos"], capture_output=True)

# ============================================================
# Interactive Mode
# ============================================================
def run_interactive():
    """Interactive mode with prompts."""
    print("\n" + "=" * 60)
    print("  🧹 Apple Photos Cleaner")
    print("  Simple & Reliable Version")
    print("=" * 60)
    
    # Description
    default_desc = "banking, payments, and messaging screenshots (Instagram, WhatsApp, iMessage)"
    print(f"\n1. What photos should I find?")
    print(f"   Default: {default_desc[:60]}...")
    desc = input("   Your description (or Enter for default): ").strip()
    if not desc:
        desc = default_desc
    
    # Limit
    print(f"\n2. How many photos to scan?")
    lim_input = input("   Number or 'all' [all]: ").strip().lower()
    limit = None
    if lim_input and lim_input != "all":
        try:
            limit = int(lim_input)
        except ValueError:
            print("   Invalid number, scanning all photos")
    
    # Dashboard
    print(f"\n3. Visual dashboard (web UI)?")
    use_dashboard = input("   Y/n [Y]: ").strip().lower() != "n"
    
    # Dry run
    print(f"\n4. Dry run (preview only, don't add to album)?")
    dry_run = input("   y/N [N]: ").strip().lower() == "y"
    
    # Confirm
    print(f"\n" + "-" * 40)
    print(f"Description: {desc[:50]}...")
    print(f"Limit: {limit or 'all'}")
    print(f"Dashboard: {'Yes' if use_dashboard else 'No'}")
    print(f"Mode: {'DRY RUN (preview)' if dry_run else 'LIVE (will add to album)'}")
    print(f"-" * 40)
    
    confirm = input("\nStart scan? (Y/n): ").strip().lower()
    if confirm == "n":
        print("Cancelled.")
        return
    
    # Run scan
    scan_photos(desc, limit, dry_run, use_dashboard)

# ============================================================
# Main
# ============================================================
def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Apple Photos Cleaner - Find and organize photos with AI"
    )
    parser.add_argument(
        "description",
        nargs="?",
        help="Description of photos to find"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum photos to scan"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only, don't add matches to album"
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch visual web dashboard"
    )
    
    args = parser.parse_args()
    
    # Load API key
    load_api_key()
    
    # Check dependencies
    check_dependencies()
    
    # Run
    if args.description:
        # Command line mode
        scan_photos(args.description, args.limit, args.dry_run, args.dashboard)
    else:
        # Interactive mode
        run_interactive()

if __name__ == "__main__":
    main()
