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

## Use it with Claude Code

This repo pairs with a `/debug` skill. Point it at a project and, for a web app, it self-installs this tool (cloning this repo if it isn't already local), generates the config from your routes, opens the map, and runs a systematic debugging methodology alongside it — reading logged bugs, fixing them in the codebase, and marking them fixed. For non-web projects it just runs the methodology.
