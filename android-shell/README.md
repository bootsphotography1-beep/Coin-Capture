# coinscope-android-shell

Scanner-only Android APK for Coinscope. Dark gold UI. Sideload on Android 6+.

The app does **not** bake in a Mac IP. First launch opens **Settings** — type the Mac’s Wi‑Fi IP and port `8000`. Camera permission is requested on start. Photos upload to the Mac; rarity cards render on the Mac dashboard.

## Build a debug APK

```bash
cd android-shell
npm install
npm run build:debug
# android/app/build/outputs/apk/debug/app-debug.apk
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

`build:debug` copies `backend/static/scanner.html` + `styles.css` into `www/`, then `cap sync`.

## Networking

- Phone and Mac on the same Wi‑Fi
- Mac firewall allows TCP 8000
- Cleartext HTTP is enabled (LAN only)

## License

MIT
