# Coinscope

Local coin scanner for US pocket change.

Phone (scanner-only APK) → Mac (FastAPI dashboard) → Windows RTX 4070 Ti or the Mac (Ollama) → year collections on the dashboard.

Target: about **4 seconds** from upload to rare / notable / common once the vision model is warm. You can keep scanning; uploads sit in a GPU queue.

```
┌──────────────┐   Wi-Fi    ┌──────────────────┐   LAN HTTP    ┌─────────────────────┐
│ Samsung APK  │ ─────────► │ Mac              │ ────────────► │ Windows (optional)  │
│ camera only  │  :8000     │ FastAPI + SQLite │  :11434       │ Ollama qwen2.5vl:7b │
│ IP + port    │            │ live dashboard   │               │ RTX 4070 Ti         │
└──────────────┘            └────────┬─────────┘               └─────────────────────┘
                                     │ WebSocket
                                     ▼
                               Year collections
                               Cropped coin cards
```

No cloud. No API keys. Sideload the APK.

## What you get

- In-app **Settings** on the phone: Mac IP + port + token
- First-run **wizard** on the Mac: one computer or two
- Coin is **cropped** (circle on a dark background) before it hits the dashboard
- Collections **grouped by year**
- Rare / notable / common badge + LLM value range
- Queue: capture the next coin while the 4070 Ti finishes the last one

## Model

Default: **`qwen2.5vl:7b`** — best speed/accuracy for dates and mint marks on a 4070 Ti (~6 GB weights). US pocket-change rarity uses a local canonical table so a second LLM call is skipped on common coins.

## Setup

### A. Mac (dashboard)

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
python run.py
# http://0.0.0.0:8000
```

Open **http://localhost:8000/dashboard**. The wizard asks:

1. **One computer** — Ollama on this Mac (`127.0.0.1:11434`)
2. **Two computers** — this Mac stays the dashboard; enter the **Windows IP** (Ollama port `11434`)

Allow port **8000** in the Mac firewall (System Settings → Network → Firewall).

### B. Windows GPU box (two-computer)

1. Install [Ollama for Windows](https://ollama.com/download)
2. Pull the vision model:

```bat
ollama pull qwen2.5vl:7b
```

3. Listen on the LAN (PowerShell **as Administrator**, then restart Ollama from the tray):

```bat
setx OLLAMA_HOST 0.0.0.0
```

4. Windows Firewall → inbound TCP **11434**
5. Same Wi‑Fi as the Mac. In the Mac wizard, type this PC’s IPv4 (`ipconfig`)

### C. One-computer (Ollama on the Mac)

```bash
ollama pull qwen2.5vl:7b
python run.py
```

Leave Ollama on localhost. The phone still talks only to the Mac.

### D. Android APK (sideload, Android 6+)

```bash
cd android-shell
npm install
npm run build:debug
# APK: android/app/build/outputs/apk/debug/app-debug.apk
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

On the phone: **Settings → Mac IP + port 8000**. Token defaults to `coinscope-dev-token-change-me` unless you set `COINSCOPE_TOKEN` on the Mac.

The phone is scanner-only. Cards, rarity, and year collections appear on the Mac dashboard.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `COINSCOPE_HOST` | `0.0.0.0` | Bind address |
| `COINSCOPE_PORT` | `8000` | Dashboard / API port |
| `COINSCOPE_TOKEN` | `coinscope-dev-token-change-me` | Shared secret |
| `COINSCOPE_MODEL` | `qwen2.5vl:7b` | Vision model |
| `OLLAMA_BASE` | from wizard, else `http://127.0.0.1:11434` | GPU box URL |
| `COINSCOPE_WORKERS` | `1` | Keep at 1 so the 4070 Ti is not oversubscribed |
| `COINSCOPE_IDENTIFY_TIMEOUT` | `25` | Seconds for one vision call |

Wizard settings live in `data/setup.json` (not committed). Env vars override the wizard.

## Layout

```
run.py
backend/           FastAPI, crop, canonical US DB, jobs
android-shell/     Capacitor APK (local scanner + settings)
data/              SQLite + cropped photos (created at runtime)
```

## Limitations

- Photo-only ID and condition — not a professional grade
- US circulating coinage first; world coins / errors need a later pass
- Value ranges are screening estimates from the local table + model, not live auction comps
- First inference after Ollama starts can be slower (weights loading). Later scans should be near the 4s target on a 4070 Ti

## License

MIT
