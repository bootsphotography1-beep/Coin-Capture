# Coinscope — APK plausibility audit

**Verdict: the architecture can work on Android. The current APK cannot ship as a fully fledged app.**

What exists today is a **LAN prototype**: a phone camera page talks to a computer running FastAPI, which calls a local Ollama model, then a browser dashboard shows results. That pipeline is real and is the right shape. The Android piece is a Capacitor WebView that loads the Mac’s `/scan` page. It is not a self-contained app, it is not configurable at runtime, and several bugs would stop a first install from working.

---

## How the system is supposed to work

```
Android phone (camera)
        │  Wi-Fi  POST /api/upload
        ▼
Computer A — FastAPI + SQLite + dashboard
        │  HTTP  OLLAMA_BASE (default localhost)
        ▼
Computer B (or same machine) — Ollama vision + optional text model
        │
        ▼
Dashboard (browser on the computer) via WebSocket /ws
```

This matches the goal: phone captures → backend on one machine → model on the same or another machine → result on a dashboard.

The phone never talks to Ollama. That is correct. Heavy inference stays on the computer.

---

## Scorecard

| Area | Status | Notes |
|---|---|---|
| End-to-end idea (phone → PC → model → dashboard) | Plausible | Right split of work |
| Installable Android APK | Fragile | Capacitor shell only; URL baked in at build time |
| Camera capture on phone | Partial | Permission + auto-detect bugs |
| Backend job pipeline | Mostly real | Identify → condition → rarity → comparables |
| Live dashboard | **Broken** | Invalid JavaScript; page will not run |
| Runtime backend URL / token in the APK | Missing | Must rebuild to change Mac IP |
| Phone-side results UI | Missing | APK is scanner-only; dashboard is Mac-only |
| Clean native frontend | No | Default Capacitor icon, spinner splash, remote HTML |
| Auth / multi-user / Play Store | Not present | Shared LAN token only |
| Tests | None | One synthetic PNG, no automated tests |

---

## Will it work as an APK on Android?

**Yes, as a kiosk browser for a Mac on the same Wi-Fi — after several fixes and a rebuild per network.**

**No, as a fully fledged Android product** with bundled UI, in-app settings, on-device results, and reliable camera/network handling.

### What the APK actually is

`android-shell/` is Capacitor 6 wrapping `www/index.html`. That file waits 800ms, then sends the WebView to `/scan` on whatever host is in `capacitor.config.json → server.url`.

Current URL:

```json
"url": "http://10.0.2.2:8000"
```

`10.0.2.2` is **only** the Android emulator’s alias for the host machine. On a real Samsung it will not reach your Mac. You must put the Mac’s LAN IP in that file, run `npx cap sync android`, and rebuild. There is no in-app field to type the IP.

If the Mac is down, the loader does **not** stay put (despite the comment). It always navigates, and the user gets a WebView error page.

### Android blockers (would fail a first sideload)

1. **Cleartext HTTP on Android 9+.** Target SDK is 34. The committed `AndroidManifest.xml` has no `android:usesCleartextTraffic="true"`. Capacitor may inject this during `cap sync` because `server.cleartext` is true — that must be verified after sync. Without it, `http://192.168.x.x:8000` is blocked.

2. **Camera runtime permission.** `CAMERA` is in the manifest. Nothing requests it at runtime. `getUserMedia` in a WebView will fail on modern Android until the OS permission is granted. There is no `@capacitor/camera` (or similar) plugin.

3. **Hardcoded emulator IP.** Real phones cannot use `10.0.2.2`.

4. **Token mismatch.** Scanner falls back to `coinscope-dev-token-change-me`. The APK opens `/scan` with no `?token=`. If `COINSCOPE_TOKEN` is set on the Mac, every upload returns 401.

5. **Must `npm install && npx cap sync` before Gradle.** Capacitor assets and `node_modules/@capacitor/android` are not in the repo (correctly gitignored). A raw `./gradlew assembleDebug` on a fresh clone will fail.

6. **Missing `res/values/colors.xml`.** `styles.xml` references `@color/colorPrimary`, `colorPrimaryDark`, and `colorAccent`. Those colors are not defined in the app module. Likely Gradle resource-link failure.

7. **Release APK.** `build:release` has no signing config. Debug APK only, unless you add a keystore.

### What would work if the above were fixed

- Same-origin WebView (Capacitor `server.url`) so `/api/upload`, `/api/health`, and relative `/static` URLs work without CORS.
- `INTERNET` permission is declared.
- Mixed-content / cleartext flags are in `capacitor.config.json`.
- Scanner HTML is mobile-sized (square viewfinder, big capture button, dark theme).
- Backend already accepts uploads, queues work, and broadcasts dashboard events.

So: **prototype APK = plausible. Product APK = not this codebase yet.**

---

## Backend integration (computer + local model)

The FastAPI app is a real local backend, not a mock.

**Does work (design):**

- `POST /api/upload` saves the photo, creates a scan row, enqueues a worker.
- Worker: vision identify → photo condition estimate → rarity (canonical DB, then specialist model, then generic model) → Wikipedia/auction links.
- `OLLAMA_BASE` can point at another computer. That is the “reroute to another machine” path. Default is `http://127.0.0.1:11434` (Ollama on the same box as FastAPI).
- Dashboard is meant to get live updates over `/ws`.
- SQLite + photo files stay on the backend machine.

**Does not work / will surprise you:**

| Issue | Why it matters |
|---|---|
| `run.py` loads `app:app` with `backend/` on `sys.path`, but `app.py` uses package-relative `from . import …` | Typical result: `ImportError: attempted relative import with no known parent package`. Launcher and import style disagree. |
| `jobs.py` still does `from canonical import lookup_canonical` | Breaks if you start the app as `backend.app`. Rarity short-circuit never runs in that mode. |
| README default model is `llava:7b`; `config.py` default is `moondream` | Fresh install follows README, code loads a different model. |
| Specialist model is hardcoded to `llama3.1:8b` | `COINSCOPE_RARITY_MODEL` is mentioned in a comment but **not** read from the environment. Second model must be pulled or every non-canonical coin falls through. |
| Dashboard JS is invalid | `const TOKEN=*** URLSearchParams(...)` — not legal JavaScript. The dashboard script will not parse, so **results never render**. Scanner correctly uses `new URLSearchParams`. |
| Scanner auto-capture is dead | `startCamera()` calls `detectionLoop`, but the function is named `detectLoop`. Manual Capture still works. |
| No CORS middleware | Fine while the APK loads the Mac origin. Breaks if the APK is ever a local `capacitor://` page calling the Mac. |
| Health endpoint is unauthenticated; uploads are not | Fine on a trusted LAN. Not fine if port 8000 is reachable beyond the house. |
| Comparables are Wikipedia + search links, not sold prices | Honest limitation; not live auction data. |
| Canonical DB is a small US-only list | World coins and varieties always hit the LLM. Some canonical mintage/value rows are rough. |
| `find_duplicates_by_fingerprint` ignores `threshold_minutes` | Duplicate flagging is weaker than documented. |
| No automated tests | `tests/` is one PNG. |

**Model routing today**

1. Phone → FastAPI (`COINSCOPE_HOST:PORT`, default `0.0.0.0:8000`).
2. FastAPI → Ollama (`OLLAMA_BASE` + `COINSCOPE_MODEL`).
3. Optional second Ollama model `llama3.1:8b` on the **same** `OLLAMA_BASE` for rarity.
4. Dashboard ← FastAPI WebSocket.

To put the model on another computer: run Ollama there, set `OLLAMA_BASE=http://<gpu-pc>:11434` on the FastAPI machine, allow that port on the firewall. The phone still only needs the FastAPI IP. That part of the design is sound.

---

## Frontend cleanliness (APK + scanner + dashboard)

The web UI has a consistent dark theme and gold accent. It is readable, not “native Android.”

**Scanner (`/scan`) — what the APK shows**

- Usable layout: header, square camera, two buttons, status line, recent-scan list.
- No settings (server IP, token, camera facing, auto vs manual).
- No flip-coin affordance beyond a status sentence.
- Queue rows never leave “uploading…” unless polling happens to match `q_<scan_id>` (reverse attach can reuse an id and skip a row).
- Auto-detect overlay exists but the loop is never started (see bug above).
- Hardcoded default token in page source.

**APK chrome**

- Default Capacitor Android robot icon and generic splash PNGs — not a coin product.
- Loader is a spinner and one line of text. No retry, no IP field, no error copy.
- No in-app dashboard, history, or result cards on the phone.
- `MainActivity` is empty `BridgeActivity`. No permission prompt, no connectivity UI.

**Dashboard (`/dashboard`) — Mac browser**

- Card grid, search, CSV/TXT export, WebSocket refresh — good prototype.
- Entire script is dead because of the `***` token line.
- No login, no scan detail page, no delete, no user notes UI (notes exist in the API only).
- Canonical badge checks `rar.source === "canonical"` but the worker stores `rarity_source`.

For a “clean APK frontend,” this needs: a settings screen, a real connecting/error state, runtime camera permission, coin-themed icon/splash, and either a phone dashboard or a clear “results appear on the computer” empty state.

---

## What “fully fledged” would still need

Minimum to match the stated product (phone APK + backend on a PC + model on that PC or another + dashboard):

1. Fix launcher imports so `python run.py` actually starts.
2. Fix dashboard JS and scanner `detectionLoop`.
3. In-APK **server URL + token** (saved locally), with a connection test.
4. Runtime camera permission + cleartext/network security config that survives `cap sync`.
5. Honest connecting / offline / wrong-IP UI (do not redirect blindly).
6. Results visible somewhere reliable: Mac dashboard **and/or** a phone results tab fed by `/api/scans` + `/ws`.
7. Documented two-machine setup: FastAPI host vs Ollama host, both models to pull, firewall ports.
8. Align README vs `COINSCOPE_MODEL` vs specialist model.
9. Debug APK signing path that a Samsung can sideload; optional release keystore.
10. A short device test: same Wi-Fi, Mac firewall open, Ollama up, one obverse + reverse, card appears on the dashboard.

That is still a **home-LAN app**, not a Play Store product (accounts, HTTPS, abuse controls, on-device ML, etc.).

---

## Questions (needed before building the real APK)

Please answer these so the next pass is not guesswork.

### Topology
1. Does FastAPI run on the same computer as Ollama, or should the model always live on a second machine?
2. If two machines: Windows, Mac, or Linux for FastAPI? For Ollama? Any NVIDIA GPU?
3. Must the phone and backend stay on the same Wi-Fi, or do you need access away from home (Tailscale / HTTPS / public host)?

### Android app
4. Should the APK be **scanner-only** (results only on the computer dashboard), or should the phone also show identification, rarity, and photos?
5. Do you want a **Settings** screen to type the computer IP, port, and token (recommended), or is rebuilding the APK per house OK?
6. Sideload debug APK only, or a signed release for Play Store / internal sharing?
7. Target phones: Samsung only, or any Android 6+ (minSdk 22)?
8. Auto-capture when a coin is steady, manual shutter only, or both?

### Model
9. Which vision model should be the default: `llava:7b` (README) or `moondream` (code)?
10. Should `llama3.1:8b` rarity specialist be required, optional, or removed?
11. US pocket change only, or world coins / errors / varieties as a first-class goal?
12. Are LLM value ranges acceptable as “screening estimates,” or do you need a real price API (eBay, PCGS) later?

### Product
13. Single household, shared token — or multiple users / collections?
14. Should scans persist forever on the computer, and do you need delete / edit notes / merge duplicates in the UI?
15. Any branding (app name, icon, colors) beyond the current dark + gold Coinscope look?

---

## File-by-file notes (short)

| File | Role | Audit note |
|---|---|---|
| `run.py` | Uvicorn launcher | Import path fights relative imports in `backend/` |
| `backend/app.py` | HTTP + WebSocket | Solid route set; no CORS; token on APIs; photo path confined to `PHOTOS_DIR` |
| `backend/config.py` | Env settings | `OLLAMA_BASE` supports remote model host; model default ≠ README; no `RARITY_MODEL` |
| `backend/jobs.py` | Queue + WS broadcast | Real pipeline; bad `canonical` import; reverse re-identify is best-effort |
| `backend/ollama_client.py` | Vision JSON | Reasonable tolerant parser; 120s timeout; depends on Ollama `/api/chat` |
| `backend/rarity.py` / `rarity_specialist.py` | Text rarity | Specialist model name not wired to env; duplicate JSON-repair logic |
| `backend/canonical.py` | US short-circuit DB | Useful for common series; some mintage/value figures are approximate |
| `backend/condition.py` | Photo heuristics | Honest “not a grade”; sharpness/wear proxies only |
| `backend/comparables.py` | Wiki + auction links | Network optional; not sold-comp data |
| `backend/db.py` | SQLite | Fine for LAN; WAL; JSON blobs; duplicate time window unused |
| `backend/static/scanner.html` | Phone UI | Camera + upload real; `detectionLoop` typo; no settings |
| `backend/static/dashboard.html` | Results UI | **Parse error on TOKEN line**; otherwise a decent card grid |
| `backend/static/styles.css` | Shared theme | Clean enough for a prototype; not Material/Android |
| `android-shell/www/index.html` | APK entry | Blind redirect; no retry/settings |
| `android-shell/capacitor.config.json` | WebView target | Emulator IP; cleartext intended |
| `android-shell/android/…` | Native project | Incomplete colors; no runtime permission; default icon |

---

## Bottom line

Coinscope is a **credible local-network prototype**, not a finished Android app.

- **Plausible:** phone photo → computer API → Ollama (local or another PC) → dashboard.
- **Not plausible as-is:** install APK on a Samsung, point it at a Mac, and get a reliable full product with a clean native frontend.
- **Highest-impact bugs:** dashboard script does not parse; `python run.py` import style is inconsistent; APK URL is emulator-only; camera permission never requested; auto-detect function name mismatch.

Once the questions above are answered, the next work is: fix the P0 bugs, add an APK settings + connection screen, request camera permission, and decide whether results live on the phone, the computer, or both.
