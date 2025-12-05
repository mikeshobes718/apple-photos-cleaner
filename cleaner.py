#!/usr/bin/env python3
"""
Apple Photos Cleaner - Simple & Reliable
Focuses on accuracy over speed. Processes one photo at a time.
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
LOG_PATH = os.path.expanduser("~/Documents/logs/photo_cleaner.log")
ALBUM_NAME = "🤖 AI Matches - To Delete"
MODEL = "gpt-4o-mini"
MAX_IMAGE_SIZE = 512
CONFIDENCE_THRESHOLD = 0.7

# File types to skip (videos, RAW files)
SKIP_EXTENSIONS = {'.mov', '.mp4', '.m4v', '.avi', '.3gp', '.mkv', '.webm',
                   '.raw', '.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2'}

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

def add_to_album(photo_uuid: str) -> bool:
    """Add photo to the matches album using AppleScript."""
    script = f'''
    tell application "Photos"
        -- Get or create album
        set albumName to "{ALBUM_NAME}"
        set targetAlbum to missing value
        
        try
            set targetAlbum to album albumName
        on error
            set targetAlbum to make new album named albumName
        end try
        
        -- Add photo
        try
            set thePhoto to media item id "{photo_uuid}"
            add {{thePhoto}} to targetAlbum
            return "success"
        on error errMsg
            return "error: " & errMsg
        end try
    end tell
    '''
    
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30
        )
        return "success" in result.stdout.lower()
    except subprocess.TimeoutExpired:
        log("WARNING: AppleScript timed out adding to album")
        return False
    except Exception as e:
        log(f"WARNING: AppleScript error: {e}")
        return False

# ============================================================
# Main Scanner
# ============================================================
def scan_photos(description: str, limit: int = None, dry_run: bool = False):
    """
    Main scanning function.
    
    Args:
        description: What to look for in photos
        limit: Max photos to scan (None = all)
        dry_run: If True, don't add matches to album
    """
    import openai
    import osxphotos
    
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
        return
    
    log(f"Library loaded: {len(all_photos):,} total, {len(photos):,} local in {load_time:.1f}s")
    
    # Apply limit
    if limit:
        photos = photos[:limit]
    
    total = len(photos)
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
    
    # Process each photo
    for i, photo in enumerate(photos, 1):
        filename = photo.original_filename or f"photo_{i}"
        
        # Progress indicator
        progress = f"[{i}/{total}]"
        
        # Encode photo
        image_data, error = encode_photo(photo)
        
        if error:
            if "Skipped" in error:
                stats["skipped"] += 1
            else:
                stats["errors"] += 1
                log(f"ERROR: {error}")
            print(f"   {progress} ⏭️  {filename[:40]}")
            continue
        
        # Analyze with AI
        result = analyze_photo(client, image_data, description)
        stats["scanned"] += 1
        stats["cost"] += cost_per_image
        
        is_match = result["match"] and result["confidence"] >= CONFIDENCE_THRESHOLD
        
        if is_match:
            stats["matched"] += 1
            matches.append({
                "uuid": photo.uuid,
                "filename": filename,
                "confidence": result["confidence"],
                "reason": result["reason"]
            })
            
            print(f"   {progress} ⚡ MATCH: {filename[:35]} ({result['confidence']:.0%})")
            print(f"           └─ {result['reason']}")
            log(f"MATCH: {filename} | conf={result['confidence']:.2f} | {result['reason']}")
            
            # Add to album (unless dry run)
            if not dry_run:
                if add_to_album(photo.uuid):
                    stats["added_to_album"] += 1
                    print(f"           └─ ✓ Added to album")
                else:
                    print(f"           └─ ⚠ Failed to add to album")
        else:
            print(f"   {progress} ○  {filename[:40]}")
        
        # Show progress every 10 photos
        if i % 10 == 0:
            elapsed = time.time() - start_time - load_time
            rate = stats["scanned"] / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            print(f"\n   📊 Progress: {i}/{total} | Rate: {rate:.1f}/sec | ETA: {remaining/60:.0f} min\n")
    
    # Summary
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
    
    # Dry run
    print(f"\n3. Dry run (preview only, don't modify)?")
    dry_run = input("   y/N [N]: ").strip().lower() == "y"
    
    # Confirm
    print(f"\n" + "-" * 40)
    print(f"Description: {desc[:50]}...")
    print(f"Limit: {limit or 'all'}")
    print(f"Mode: {'DRY RUN (preview)' if dry_run else 'LIVE (will add to album)'}")
    print(f"-" * 40)
    
    confirm = input("\nStart scan? (Y/n): ").strip().lower()
    if confirm == "n":
        print("Cancelled.")
        return
    
    # Run scan
    scan_photos(desc, limit, dry_run)

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
    
    args = parser.parse_args()
    
    # Load API key
    load_api_key()
    
    # Check dependencies
    check_dependencies()
    
    # Run
    if args.description:
        # Command line mode
        scan_photos(args.description, args.limit, args.dry_run)
    else:
        # Interactive mode
        run_interactive()

if __name__ == "__main__":
    main()
