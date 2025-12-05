# 🧹 Apple Photos Cleaner

Automatically find and delete photos from Apple Photos using AI.
**Supports Cloud AI (OpenAI) or Free Local AI (Ollama).**

## 💰 Cost vs. Free

| Option | Cost | Speed | Quality | Requirements |
|--------|------|-------|---------|--------------|
| **OpenAI** (gpt-4o-mini) | ~$0.10 per 1k photos | ⚡️ Fast | 🌟 Best | API Key |
| **Ollama** (moondream) | **FREE** | 🐢 Slower | Good | 8GB+ RAM |

---

## Setup

### Prerequisites

- Python 3.9+
- Access to the macOS Photos library.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/mikeshobes718/apple-photos-cleaner.git
    cd apple-photos-cleaner
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Create/update your shared Keys file (JSON supported)**:
    The script loads secrets from `/Users/mike/Documents/Keys/.env`. It can be JSON or dotenv. Recommended JSON format:

    ```json
    {
      "OPENAI_API_KEY": "sk-YOUR_API_KEY_HERE"
    }
    ```

### (Optional) Local AI with Ollama

If you prefer to run analysis locally for free, you can use Ollama.

1.  [Install Ollama](https://ollama.ai).
2.  Pull a vision model, such as `moondream`:
    ```bash
    ollama pull moondream
    ```
3.  Ensure the Ollama application is running before starting the script.

## Usage

The script defaults to an easy-to-use interactive mode.

```bash
python3 photo_cleaner.py
```

**Interactive Mode (Best for starting out):**
```bash
python3.12 scripts/photo_cleaner_interactive.py
```
*It will ask you which AI backend you want to use.*

**Command Line Mode:**

```bash
# OpenAI Mode (Default)
python3.12 scripts/photo_cleaner.py "blurry screenshots" --dry-run

# Local AI Mode
python3.12 scripts/photo_cleaner.py "old receipts" --backend ollama --model moondream
```

## Usage Examples

| Description | What it finds |
|-------------|---------------|
| `"blurry screenshots"` | Low quality screenshots |
| `"old receipts and documents"` | Photos of paper receipts, invoices, bills |
| `"memes"` | Meme images |
| `"duplicate selfies"` | Similar/repeated selfie photos |
| `"photos with just text"` | Screenshots of text messages, notes |

## Safety Features

- Photos match go to **Recently Deleted** (recoverable for 30 days)
- `--dry-run` mode to preview before deleting
- `--confirm-each` for manual review
- Confidence threshold prevents false positives

## Troubleshooting

**"Ollama is not running"**
- Open a terminal and run `ollama serve`
- Or open the Ollama app on your Mac

**"Could not load photos library"**
- Ensure Photos app is not open during scanning
- Grant **Full Disk Access** to Terminal/Python (System Settings → Privacy & Security)

**"AppleScript error"**
- Grant **Automation** permission to Terminal/Python
