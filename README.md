# Project Debug Map

A single-file, zero-dependency debugging surface for websites **and iOS apps**. It renders every page of a site (and every screen of an app) as a node map grouped by category, opens any web page at **desktop / tablet / mobile** sizes (against production or your local dev server), drives an **iOS Simulator** the same way (live view, deep-link navigation, app console), and tracks bugs **per page / per screen** on a Kanban board (new → review → fixed) with pasted screenshots.

Built to walk a product surface-by-surface, log what's broken on each device, and feed those bugs back to whoever (or whatever) is fixing them.

## Run it

It's just `index.html`. Serve the folder so it can `fetch()` a project config (opening the file directly with `file://` blocks that):

```bash
python3 -m http.server 8765 --bind 127.0.0.1
# then open:
# http://127.0.0.1:8765/index.html?config=<your-project>.project.json
```

Without `?config=`, it falls back to `localStorage` → `project.json` in this folder.

## Configure a project

Drop a `<slug>.project.json` next to `index.html`:

```json
{
  "name": "My Site — Website",
  "environments": { "production": "https://live.example.com", "local": "http://localhost:3000" },
  "pages": [
    { "path": "/", "name": "Home", "category": "core", "description": "Landing page" }
  ],
  "navigation": [ ["/", "/about"] ],
  "ios": {
    "appName": "My App",
    "bundleId": "com.example.myapp",
    "urlScheme": "myapp",
    "screens": [
      { "name": "Home", "deepLink": "myapp:///", "category": "core", "description": "Landing screen" },
      { "name": "Login", "deepLink": "myapp:///login", "category": "auth", "description": "Sign in" }
    ],
    "navigation": [ ["Home", "Login"] ]
  }
}
```

- The **first** key in `environments` is the default (put the live site first).
- Dynamic routes need **real** example values (`/listing/123`, not `/listing/:id`).
- `navigation` draws edges between pages on the map.
- `ios` is optional; a config can be web-only, iOS-only (`"environments": {}, "pages": []`), or both — iOS screens get their own purple columns next to the web ones.
- iOS screen `name` is the identity (bugs key on `ios::<name>`); `deepLink` is optional per screen; `ios.navigation` pairs are screen names.

See `project.example.json` for a starting point.

## Features

- **Site + app map** — pages and iOS screens as nodes, grouped by category, with navigation/flow edges.
- **Device views** — open any page at desktop (1550×1000), tablet (768×1024), or mobile (390×844); open the app through the **iOS Sim** lane.
- **Environment toggle** — flip each page between production and local.
- **Per-page bug tracker** — Kanban columns (new / review / fixed), one bug list per page/screen, paste screenshots onto cards. Bugs persist in `localStorage` keyed by project name.
- **Agent bridge** — a tool/agent can read and update bugs from the page (iOS screens use `page: "ios::<Screen Name>"`):
  ```js
  window.bugSystem.getBugs()
  window.bugSystem.addBug(page, text)
  window.bugSystem.setStatus(page, text, 'fixed' | 'review' | 'new')
  ```

## iOS Simulator lane

The map drives the iOS Simulator through a local bridge script that talks to `xcrun simctl` on your behalf (browsers cannot run shell commands directly). Requires Xcode command line tools.

### Setup

```bash
# 1. Start the bridge in a terminal tab — keep it running
python3 sim-bridge.py

# 2. Serve the debug map (same as usual)
python3 -m http.server 8765 --bind 127.0.0.1
```

### Usage

1. Open the debug map and click **iOS Sim** in the device toggle bar.
2. The panel connects to the bridge (green "Connected"), picks the booted simulator (or **Boot** one), and immediately shows a **live view** of the simulator screen.
3. Your app's bundle id is prefilled from the config (or pick from the installed-apps dropdown). **Launch** starts it fresh with its console attached — stdout/stderr (`print`, `NSLog`) stream into the **Console** pane at the bottom, with errors and warnings highlighted, a text filter, and an `os_log` button for unified-log output (`Logger`).
4. Config-defined screens appear in the left rail (and as purple nodes on the map). **Clicking a screen deep-links the simulator to it** and auto-captures a thumbnail — the app equivalent of clicking a page node. Screens without deep links work as manual capture targets: navigate in the Simulator, name the screen, **Capture Screen**.
5. Bugs auto-appear per screen — describe, paste screenshots, move through new / review / fixed exactly like web pages.

### How it works

- `sim-bridge.py` runs a small threaded HTTP server on `127.0.0.1:8766`.
- Endpoints: `GET /api/status`, `/api/simulators`, `/api/apps?udid=`, `/api/screenshot?udid=`, `/api/logs?lines=`, `/api/oslog?udid=&process=&minutes=`, `/api/crashes?process=`; `POST /api/boot`, `/api/launch`, `/api/terminate`, `/api/openurl`, `/api/install`.
- **Launch** uses `simctl launch --console-pty`, so the app's stdout/stderr stream into a logfile the map tails; `os_log` runs `log show` on demand; `/api/crashes` surfaces recent `.ips` reports from `~/Library/Logs/DiagnosticReports`.
- Screenshots are returned as base64 PNG data URIs, stored in `localStorage` alongside bugs.
- Bugs for iOS screens use paths prefixed with `ios::` (e.g. `ios::Login`), so they're tracked separately from web-page bugs but live in the same Kanban board.
- The live view is screenshot polling (~1.2s), not video — interaction (taps/typing) still happens in the Simulator app itself, which is the point: the Simulator IS the device.

### Limitations

- No automated full-app crawl (no `XCUITest` integration).
- The bridge does not inject touches; use the Simulator directly for interaction.
- Deep links require the app to register a URL scheme (Expo apps get this from `expo.scheme` automatically).

## Use it with Claude Code

This repo pairs with a `/debug` skill. Point it at a project and, for a web app or an iOS app, it self-installs this tool (cloning this repo if it isn't already local), generates the config from your routes/screens, opens the map, starts the bridge, and runs a systematic debugging methodology alongside it — reading logged bugs, fixing them in the codebase, and marking them fixed. For other projects it just runs the methodology.
