# coinscope-android-shell

Native Android wrapper for the [Coinscope](https://github.com/bootsphotography1-beep/Coin-Capture) local coin scanner.

Capacitor scaffold that bundles the Coinscope scanner PWA into an Android APK. The APK talks to a FastAPI backend running on the user's Mac over Wi-Fi — no cloud, no API keys, no per-scan cost.

## What's in here

- `capacitor.config.json` — Android app config. **Edit `server.url` to point at your Mac's LAN IP** before building.
- `www/index.html` — Splash loader that redirects to `/scan` on the Mac backend.
- `android/` — Complete native Android project (Gradle, Java, manifest, resources). Camera permission is already declared in `AndroidManifest.xml`.

## Build

```bash
# 1. Edit capacitor.config.json — replace 10.0.2.2 with your Mac's LAN IP
#    (find it with `ifconfig | grep "inet " | grep -v 127.0.0.1`)

# 2. Install deps
npm install

# 3. Sync web assets into the Android project
npx cap sync android

# 4a. Open in Android Studio (recommended for first build)
npx cap open android
#    Then in Android Studio: Build → Run

# 4b. Or build from the command line:
npm run build:debug
#    APK lands at android/app/build/outputs/apk/debug/app-debug.apk
```

## Install the APK on your Samsung

```bash
# With USB debugging enabled on the phone:
adb install android/app/build/outputs/apk/debug/app-debug.apk
```

Or just drag the APK to the phone via Android File Transfer.

## How it talks to the Mac

The APK launches a full-screen WebView pointing at `capacitor.config.json → server.url`. When the Mac's FastAPI backend is running on port 8000, the scanner page loads and you can scan coins. Photo uploads POST to `/api/upload` on the Mac; live results stream back over the same WebSocket.

If the Mac is offline, the loader stays visible (no browser error page).

## Networking requirements

- Mac and Samsung on the same Wi-Fi network
- Mac firewall allows inbound connections on port 8000
- `http://<mac-ip>:8000` reachable from the phone (test in Chrome first)

## Part of the larger project

This package is one piece of the Coinscope repo:

```
Coin-Capture/
├── backend/           # FastAPI + SQLite + Ollama
├── data/              # SQLite db + coin photos
├── android-shell/     # ← this package
├── run.py             # Mac launcher
└── tests/             # Synthetic test coin
```

See the [root README](https://github.com/bootsphotography1-beep/Coin-Capture) for the full picture.

## License

MIT