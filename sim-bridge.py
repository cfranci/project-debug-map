#!/usr/bin/env python3
"""
sim-bridge.py — iOS Simulator bridge for Project Debug Map.

Exposes xcrun simctl commands over a local HTTP API so the browser-based
Debug Map can list simulators, boot them, and capture screenshots without
any native permissions or extra dependencies.

Usage:
    python3 sim-bridge.py

Serves on: http://127.0.0.1:8766
Keep this running in a terminal tab while using the iOS lane in the debug map.
"""

import http.server
import json
import subprocess
import tempfile
import os
import base64
from urllib.parse import urlparse, parse_qs

PORT = 8766
HOST = "127.0.0.1"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def run_cmd(cmd, timeout=30):
    """Run a shell command. Returns (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1
    except FileNotFoundError as e:
        return "", str(e), 1


def pretty_os(runtime_id):
    """Turn 'com.apple.CoreSimulator.SimRuntime.iOS-18-2' into 'iOS 18.2'."""
    s = runtime_id.replace("com.apple.CoreSimulator.SimRuntime.", "")
    parts = s.split("-")
    if len(parts) >= 3:
        os_name = parts[0]
        version = ".".join(parts[1:])
        return f"{os_name} {version}"
    return s.replace("-", " ")


def get_simulators():
    """Return {booted: [...], available: [...]} from xcrun simctl."""
    stdout, stderr, rc = run_cmd(["xcrun", "simctl", "list", "devices", "--json"])
    if rc != 0:
        return {"booted": [], "available": [], "error": stderr.strip()}

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return {"booted": [], "available": [], "error": str(e)}

    booted = []
    available = []

    for runtime_id, devices in data.get("devices", {}).items():
        os_label = pretty_os(runtime_id)
        for device in devices:
            if not device.get("isAvailable", False):
                continue
            entry = {
                "name": device["name"],
                "udid": device["udid"],
                "state": device["state"],
                "os": os_label,
            }
            if device["state"] == "Booted":
                booted.append(entry)
            else:
                available.append(entry)

    def sort_key(d):
        name = d["name"]
        if "iPhone" in name:
            return (0, name)
        if "iPad" in name:
            return (1, name)
        return (2, name)

    booted.sort(key=sort_key)
    available.sort(key=sort_key)
    return {"booted": booted, "available": available}


def boot_sim(udid):
    """Boot a simulator. Returns (ok, message)."""
    _, err, rc = run_cmd(["xcrun", "simctl", "boot", udid], timeout=60)
    if rc != 0 and "already booted" not in err.lower():
        return False, err.strip() or "Failed to boot simulator"
    return True, "ok"


def take_screenshot(udid):
    """
    Capture the current screen of the booted simulator.
    Returns (data_uri, error_string). Exactly one will be None.
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmppath = tmp.name

    try:
        _, err, rc = run_cmd(["xcrun", "simctl", "io", udid, "screenshot", tmppath], timeout=15)
        if rc != 0:
            return None, err.strip() or "Screenshot failed"

        with open(tmppath, "rb") as f:
            img_bytes = f.read()

        b64 = base64.b64encode(img_bytes).decode("ascii")
        return "data:image/png;base64," + b64, None
    finally:
        try:
            os.unlink(tmppath)
        except OSError:
            pass


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default access log; errors still print via log_error

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/status":
            sims = get_simulators()
            self.send_json({"ok": True, "booted": sims.get("booted", [])})

        elif path == "/api/simulators":
            self.send_json(get_simulators())

        elif path == "/api/screenshot":
            udid = qs.get("udid", [None])[0]
            if not udid:
                self.send_json({"ok": False, "error": "Missing udid parameter"}, 400)
                return
            img, err = take_screenshot(udid)
            if img is None:
                self.send_json({"ok": False, "error": err}, 500)
            else:
                self.send_json({"ok": True, "image": img})

        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            data = json.loads(raw_body or b"{}")
        except (json.JSONDecodeError, ValueError):
            data = {}

        if path == "/api/boot":
            udid = data.get("udid")
            if not udid:
                self.send_json({"ok": False, "error": "Missing udid"}, 400)
                return
            print(f"[sim-bridge] Booting {udid}...")
            ok, msg = boot_sim(udid)
            if ok:
                print(f"[sim-bridge] Boot succeeded")
                self.send_json({"ok": True, "message": msg})
            else:
                print(f"[sim-bridge] Boot failed: {msg}")
                self.send_json({"ok": False, "error": msg}, 500)

        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)


if __name__ == "__main__":
    server = http.server.HTTPServer((HOST, PORT), BridgeHandler)
    print(f"iOS Simulator bridge running on http://{HOST}:{PORT}")
    print("Endpoints:")
    print(f"  GET  /api/status         - bridge health + booted sims")
    print(f"  GET  /api/simulators     - list all booted + available sims")
    print(f"  POST /api/boot           - boot a sim  body: {{\"udid\":\"...\"}}")
    print(f"  GET  /api/screenshot?udid=<udid>  - capture screen as base64 PNG")
    print()
    print("Keep this running while using the iOS Simulator lane in Debug Map.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sim-bridge] Stopped.")
