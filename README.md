# Coinscope

Local-only coin scanner. Samsung camera → Wi-Fi → Mac backend → Ollama vision model → live dashboard.

```
┌──────────────┐      Wi-Fi      ┌──────────────┐      localhost      ┌──────────┐
│ Samsung app  │ ───────────────►│   Mac app    │ ──────────────────► │  Ollama  │
│  (camera +   │   POST /api/    │  (FastAPI +  │    /api/chat        │  llava:7b│
│   upload)    │   upload        │   SQLite)    │                     │          │
└──────────────┘                 └──────────────┘                     └──────────┘
                                        │   ▲
                                        ▼   │ WebSocket /ws
                                  ┌──────────────┐
                                  │   Dashboard  │
                                  │   browser    │
                                  └──────────────┘
```

Everything stays on your local network. No cloud, no API keys, no per-scan cost.

## Features

- Auto-detection of a coin in the camera viewfinder; auto-capture when steady
- Front + reverse photo capture with side-pairing
- Local vision model identifies the coin (country, denomination, series, year, mint mark, composition)
- Curated US coin database short-circuits the rarity lookup for common coins (Lincoln Wheat, Mercury Dime, Morgan Dollar, etc.)
- Photo-based condition estimate (sharpness + luma + wear signal) with confidence
- Wikipedia summary + auction source links (eBay sold, PCGS, NGC, Heritage)
- Live-updating dashboard via WebSocket
- Search, CSV export, plain-text report
- Duplicate-scan detection (same series/year/mint scanned within the session)

## Requirements

- **Mac** running macOS 12+, Python 3.10+
- **Ollama** installed locally with the `llava:7b` model pulled
- **Samsung** phone (or any Android with Chrome), camera permission granted
- Both devices on the same Wi-Fi network

## Quick start (Mac)

```bash
# 1. Pull the vision model (one-time, ~4.7 GB)
ollama pull llava:7b

# 2. Install Python deps
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt

# 3. Run the backend
python run.py
# Server listens on http://0.0.0.0:8000

# 4. Find your Mac's LAN IP (so the phone can reach it)
ifconfig | grep "inet " | grep -v 127.0.0.1
# e.g. 192.168.1.50

# 5. Open the dashboard in any browser on the Mac
open http://localhost:8000/dashboard

# 6. From the Samsung, open Chrome and visit
#    http://192.168.1.50:8000/scan
#    (Replace 192.168.1.50 with your actual Mac IP)

# Optional: set a real auth token for the LAN
export COINSCOPE_TOKEN=some-secret-string
python run.py
# Then on the phone, visit:
# http://192.168.1.50:8000/scan?token=some-secret-string
```

## V3 Android shell (Capacitor scaffold)

The `android-shell/` folder is also packaged as a standalone npm module for
rebuilding and re-publishing the Android wrapper independently.

```bash
cd android-shell
npm install

# Edit capacitor.config.json — point server.url at your Mac's LAN IP

# Sync web assets into the native project
npx cap sync android

# Build APK from the command line
npm run build:debug   # APK at android/app/build/outputs/apk/debug/app-debug.apk
npm run build:release # requires signing config

# Or open in Android Studio for full IDE workflow
npm run open
```

### Pack the Android shell as a tarball

```bash
cd android-shell
npm run pack
# → coinscope-android-shell-0.1.0.tgz (~240 KB)
```

The tarball contains only the source scaffold (Gradle, Java, resources, web
loader) — no `node_modules/`, no build outputs. Recipients run
`npm install && npx cap sync android` to regenerate `node_modules` and copy
fresh web assets.

To publish to the npm registry when you're ready:
```bash
cd android-shell
npm login
npm publish --access public
# → https://www.npmjs.com/package/coinscope-android-shell
```

### Quick start (Android app, native shell)

The `android-shell/` folder contains a Capacitor scaffold that wraps the
backend's `/scan` page into a real Android app with full-screen, no browser
chrome, and a launcher icon.

```bash
cd android-shell
npm install

# Edit capacitor.config.json to point at your Mac:
#   server.url: "http://192.168.1.50:8000"

npx cap sync android

# Open in Android Studio (Mac only — needs Android SDK)
npx cap open android
# Build → Run on your Samsung
```

## Layout

```
coinscope/
├── run.py                       # Uvicorn launcher
├── backend/
│   ├── app.py                   # HTTP + WebSocket routes
│   ├── config.py                # Env-driven settings
│   ├── db.py                    # SQLite layer
│   ├── ollama_client.py         # Vision model wrapper + JSON parser
│   ├── canonical.py             # US coin short-circuit DB
│   ├── rarity.py                # Model-based rarity report
│   ├── condition.py             # Image-only condition estimator
│   ├── comparables.py           # Wikipedia + auction source links
│   ├── jobs.py                  # Background queue + WS broadcast
│   ├── static/
│   │   ├── scanner.html         # Phone scanner page
│   │   ├── dashboard.html       # Mac dashboard
│   │   └── styles.css
│   └── requirements.txt
├── data/
│   ├── coinscope.db             # Created on first run
│   └── photos/                  # Coin photos, organized by group_id
├── android-shell/               # Capacitor Android scaffold
│   ├── capacitor.config.json    # Mac IP goes here
│   ├── www/                     # Web assets bundled into APK
│   └── android/                 # Native Android project
└── tests/
    └── test_coin_synthetic.png  # Synthetic test coin (1909-style)
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `COINSCOPE_HOST` | `0.0.0.0` | Server bind address |
| `COINSCOPE_PORT` | `8000` | Server port |
| `COINSCOPE_TOKEN` | `coinscope-dev-token-change-me` | Shared secret for the scanner |
| `COINSCOPE_MODEL` | `llava:7b` | Ollama model name |
| `OLLAMA_BASE` | `http://127.0.0.1:11434` | Ollama server URL |
| `COINSCOPE_WORKERS` | `1` | Background job threads (raise for higher throughput) |
| `COINSCOPE_CANONICAL` | `1` | Set `0` to bypass the canonical short-circuit |

## Limitations

- Photo-only identification: blurry, dark, or partial photos will produce uncertain results
- Photo-only condition: a real grade requires physical inspection (weight, luster, surfaces)
- Auction comparables: only links to public sources; full sold-listing data requires an eBay API key
- Identification accuracy: llava:7b is the smallest practical vision model. Varieties, errors, and rare dates may need a second opinion
- No authentication beyond a shared token — fine for a home LAN, not for the public internet

## License

MIT