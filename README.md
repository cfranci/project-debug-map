# Project Debug Map

A single-file, zero-dependency debugging surface for websites. It renders every page of a site as a node map grouped by category, opens any page at **desktop / tablet / mobile** sizes (against production or your local dev server), and tracks bugs **per page** on a Kanban board (new → review → fixed) with pasted screenshots.

Built to walk a site page-by-page, log what's broken on each device, and feed those bugs back to whoever (or whatever) is fixing them.

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
  "navigation": [ ["/", "/about"] ]
}
```

- The **first** key in `environments` is the default (put the live site first).
- Dynamic routes need **real** example values (`/listing/123`, not `/listing/:id`).
- `navigation` draws edges between pages on the map.

See `project.example.json` for a starting point.

## Features

- **Site map** — pages as nodes, grouped by category, with navigation edges.
- **Device views** — open any page at desktop (1550×1000), tablet (768×1024), or mobile (390×844).
- **Environment toggle** — flip each page between production and local.
- **Per-page bug tracker** — Kanban columns (new / review / fixed), one bug list per page, paste screenshots onto cards. Bugs persist in `localStorage` keyed by project name.
- **Agent bridge** — a tool/agent can read and update bugs from the page:
  ```js
  window.bugSystem.getBugs()
  window.bugSystem.addBug(page, text)
  window.bugSystem.setStatus(page, text, 'fixed' | 'review' | 'new')
  ```

## iOS Simulator support

The debug map can map and annotate iOS simulator screens alongside your web pages. This requires a local bridge script that talks to `xcrun simctl` on your behalf (browsers cannot run shell commands directly).

### Setup

```bash
# 1. Start the bridge in a terminal tab — keep it running
python3 sim-bridge.py

# 2. Serve the debug map (same as usual)
python3 -m http.server 8765 --bind 127.0.0.1
```

### Usage

1. Open the debug map in your browser (`http://127.0.0.1:8765/index.html`).
2. Click **iOS Sim** in the device toggle bar.
3. The iOS Simulator panel opens. If the bridge is running you'll see a green "Connected" status.
4. Pick a simulator from the dropdown. If it's not booted yet, click **Boot** and wait ~5 seconds, then click the refresh button.
5. In the Simulator app, navigate to the screen you want to annotate.
6. Type a name for the screen (e.g. "Login", "Home", "Checkout") in the text field.
7. Click **Capture Screen**. The screenshot appears in the preview area and is added to the gallery on the left.
8. Bugs auto-appear for that screen — type to describe what's wrong, paste screenshots, move through new / review / fixed exactly like web pages.
9. iOS screens appear as purple nodes in the D3 site map. Clicking a node opens the iOS panel and selects that screen.

### How it works

- `sim-bridge.py` runs a tiny HTTP server on `127.0.0.1:8766`.
- The browser calls `/api/simulators`, `/api/boot`, and `/api/screenshot?udid=<udid>`.
- Screenshots are returned as base64 PNG data URIs, stored in `localStorage` alongside bugs.
- Bugs for iOS screens use paths prefixed with `ios::` (e.g. `ios::Login`), so they're tracked separately from web-page bugs but live in the same Kanban board.

### v1 limitations (planned for v2)

- Screen navigation is manual: you navigate the app in the Simulator yourself, then click Capture.
- No automated full-app crawl (no `XCUITest` integration yet).
- The bridge does not inject touches; use the Simulator directly for interaction.

## Use it with Claude Code

This repo pairs with a `/debug` skill. Point it at a project and, for a web app, it self-installs this tool (cloning this repo if it isn't already local), generates the config from your routes, opens the map, and runs a systematic debugging methodology alongside it — reading logged bugs, fixing them in the codebase, and marking them fixed. For non-web projects it just runs the methodology.
