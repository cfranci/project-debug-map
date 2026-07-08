#!/usr/bin/env python3
"""
sim-bridge.py — iOS Simulator bridge for Project Debug Map.

Exposes xcrun simctl commands over a local HTTP API so the browser-based
Debug Map can drive the iOS Simulator the way it drives a website:
list/boot simulators, list installed apps, launch/terminate the app under
test, open deep links (the "click a page node" equivalent), capture
screenshots (including a live view), tail the app's console output, and
surface crash reports. No native permissions or extra dependencies.

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
import re
import glob
import base64
import threading
from urllib.parse import urlparse, parse_qs

PORT = 8766
HOST = "127.0.0.1"

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# One console-attached app at a time (per bridge). Guarded by _console_lock.
_console_lock = threading.Lock()
_console = {"proc": None, "logfile": None, "bundle_id": None, "udid": None}


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
    # Bring the Simulator app forward so the user can interact with it.
    run_cmd(["open", "-a", "Simulator"], timeout=10)
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


def list_apps(udid):
    """
    Return user-installed (and notable system) apps on the simulator.
    simctl listapps prints an OpenStep-style plist; convert via plutil,
    fall back to a regex scrape if conversion fails.
    """
    stdout, stderr, rc = run_cmd(["xcrun", "simctl", "listapps", udid])
    if rc != 0:
        return None, stderr.strip() or "listapps failed"

    apps = []
    try:
        conv = subprocess.run(
            ["plutil", "-convert", "json", "-o", "-", "--", "-"],
            input=stdout, capture_output=True, text=True, timeout=15,
        )
        if conv.returncode == 0:
            data = json.loads(conv.stdout)
            for bundle_id, info in data.items():
                apps.append({
                    "bundleId": bundle_id,
                    "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or bundle_id,
                    "executable": info.get("CFBundleExecutable") or "",
                    "type": info.get("ApplicationType") or "",
                })
    except Exception:
        pass

    if not apps:
        # Fallback: scrape bundle ids + executables from the raw plist text.
        for m in re.finditer(r'CFBundleIdentifier\s*=\s*"?([\w.\-]+)"?;', stdout):
            apps.append({"bundleId": m.group(1), "name": m.group(1), "executable": "", "type": ""})

    def sort_key(a):
        return (0 if a.get("type") == "User" else 1, a.get("name", "").lower())

    apps.sort(key=sort_key)
    return apps, None


def app_executable(udid, bundle_id):
    """Best-effort CFBundleExecutable lookup for a bundle id."""
    apps, _ = list_apps(udid)
    if not apps:
        return None
    for a in apps:
        if a["bundleId"] == bundle_id:
            return a.get("executable") or None
    return None


def stop_console():
    """Kill the console-attached launch, keep the logfile for reading."""
    with _console_lock:
        proc = _console.get("proc")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _console["proc"] = None


def launch_app(udid, bundle_id):
    """
    (Re)launch an app with its console attached. stdout/stderr (print, NSLog)
    stream into a logfile that /api/logs tails. Returns (ok, message).
    """
    stop_console()
    # Fresh launch so the console attaches from the first line.
    run_cmd(["xcrun", "simctl", "terminate", udid, bundle_id], timeout=15)

    logfile = os.path.join(tempfile.gettempdir(), f"sim-bridge-{bundle_id}.log")
    try:
        log_fh = open(logfile, "w")
    except OSError as e:
        return False, str(e)

    try:
        proc = subprocess.Popen(
            ["xcrun", "simctl", "launch", "--console-pty", udid, bundle_id],
            stdout=log_fh, stderr=subprocess.STDOUT,
        )
    except Exception as e:
        log_fh.close()
        return False, str(e)

    with _console_lock:
        _console.update({"proc": proc, "logfile": logfile, "bundle_id": bundle_id, "udid": udid})

    # If launch fails it exits almost immediately; give it a beat and check.
    try:
        proc.wait(timeout=1.5)
        with open(logfile) as f:
            tail = f.read()[-500:]
        return False, (tail.strip() or "Launch exited immediately")
    except subprocess.TimeoutExpired:
        return True, "ok"


def terminate_app(udid, bundle_id):
    stop_console()
    _, err, rc = run_cmd(["xcrun", "simctl", "terminate", udid, bundle_id], timeout=15)
    if rc != 0 and "found nothing to terminate" not in err.lower():
        return False, err.strip() or "terminate failed"
    return True, "ok"


def read_logs(lines):
    """Tail the current console logfile. Returns dict for the response."""
    with _console_lock:
        logfile = _console.get("logfile")
        bundle_id = _console.get("bundle_id")
        proc = _console.get("proc")
        running = bool(proc and proc.poll() is None)

    if not logfile or not os.path.exists(logfile):
        return {"ok": True, "lines": [], "running": False, "bundleId": None}

    try:
        with open(logfile, "r", errors="replace") as f:
            content = f.readlines()
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "lines": [l.rstrip("\n") for l in content[-lines:]],
        "running": running,
        "bundleId": bundle_id,
    }


def read_oslog(udid, process, minutes):
    """On-demand unified log (os_log/Logger) for a process. Can take a few seconds."""
    predicate = f'process == "{process}"'
    stdout, stderr, rc = run_cmd(
        ["xcrun", "simctl", "spawn", udid, "log", "show",
         "--last", f"{minutes}m", "--style", "compact", "--color", "none",
         "--predicate", predicate],
        timeout=60,
    )
    if rc != 0:
        return None, stderr.strip() or "log show failed"
    lines = [l for l in stdout.splitlines() if l.strip()]
    return lines[-800:], None


def recent_crashes(process, limit=5):
    """Recent .ips crash reports for a process from ~/Library/Logs/DiagnosticReports."""
    report_dir = os.path.expanduser("~/Library/Logs/DiagnosticReports")
    pattern = os.path.join(report_dir, f"{process}*.ips") if process else os.path.join(report_dir, "*.ips")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)[:limit]

    crashes = []
    for path in files:
        entry = {"file": os.path.basename(path), "mtime": int(os.path.getmtime(path))}
        try:
            with open(path, "r", errors="replace") as f:
                header = f.readline()
            meta = json.loads(header)
            entry["process"] = meta.get("app_name") or meta.get("procName") or ""
            entry["date"] = meta.get("timestamp", "")
            entry["exception"] = meta.get("exception", {}) if isinstance(meta.get("exception"), dict) else {}
        except Exception:
            pass
        crashes.append(entry)
    return crashes


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
            self.send_json({"ok": True, "version": 2, "booted": sims.get("booted", [])})

        elif path == "/api/simulators":
            self.send_json(get_simulators())

        elif path == "/api/apps":
            udid = qs.get("udid", [None])[0]
            if not udid:
                self.send_json({"ok": False, "error": "Missing udid parameter"}, 400)
                return
            apps, err = list_apps(udid)
            if apps is None:
                self.send_json({"ok": False, "error": err}, 500)
            else:
                self.send_json({"ok": True, "apps": apps})

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

        elif path == "/api/logs":
            try:
                lines = min(int(qs.get("lines", ["200"])[0]), 2000)
            except ValueError:
                lines = 200
            self.send_json(read_logs(lines))

        elif path == "/api/oslog":
            udid = qs.get("udid", [None])[0]
            process = qs.get("process", [None])[0]
            try:
                minutes = min(int(qs.get("minutes", ["3"])[0]), 30)
            except ValueError:
                minutes = 3
            if not udid or not process:
                self.send_json({"ok": False, "error": "Missing udid or process parameter"}, 400)
                return
            lines, err = read_oslog(udid, process, minutes)
            if lines is None:
                self.send_json({"ok": False, "error": err}, 500)
            else:
                self.send_json({"ok": True, "lines": lines})

        elif path == "/api/crashes":
            process = qs.get("process", [None])[0]
            self.send_json({"ok": True, "crashes": recent_crashes(process)})

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

        elif path == "/api/launch":
            udid, bundle_id = data.get("udid"), data.get("bundleId")
            if not udid or not bundle_id:
                self.send_json({"ok": False, "error": "Missing udid or bundleId"}, 400)
                return
            print(f"[sim-bridge] Launching {bundle_id} on {udid} (console attached)")
            ok, msg = launch_app(udid, bundle_id)
            executable = app_executable(udid, bundle_id) if ok else None
            if ok:
                self.send_json({"ok": True, "message": msg, "executable": executable})
            else:
                self.send_json({"ok": False, "error": msg}, 500)

        elif path == "/api/terminate":
            udid, bundle_id = data.get("udid"), data.get("bundleId")
            if not udid or not bundle_id:
                self.send_json({"ok": False, "error": "Missing udid or bundleId"}, 400)
                return
            ok, msg = terminate_app(udid, bundle_id)
            self.send_json({"ok": ok, "message" if ok else "error": msg}, 200 if ok else 500)

        elif path == "/api/openurl":
            udid, url = data.get("udid"), data.get("url")
            if not udid or not url:
                self.send_json({"ok": False, "error": "Missing udid or url"}, 400)
                return
            _, err, rc = run_cmd(["xcrun", "simctl", "openurl", udid, url], timeout=20)
            if rc != 0:
                self.send_json({"ok": False, "error": err.strip() or "openurl failed"}, 500)
            else:
                self.send_json({"ok": True})

        elif path == "/api/install":
            udid, app_path = data.get("udid"), data.get("path")
            if not udid or not app_path:
                self.send_json({"ok": False, "error": "Missing udid or path"}, 400)
                return
            app_path = os.path.expanduser(app_path)
            if not os.path.exists(app_path):
                self.send_json({"ok": False, "error": f"No such path: {app_path}"}, 400)
                return
            _, err, rc = run_cmd(["xcrun", "simctl", "install", udid, app_path], timeout=120)
            if rc != 0:
                self.send_json({"ok": False, "error": err.strip() or "install failed"}, 500)
            else:
                self.send_json({"ok": True})

        else:
            self.send_json({"ok": False, "error": "Not found"}, 404)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer((HOST, PORT), BridgeHandler)
    print(f"iOS Simulator bridge v2 running on http://{HOST}:{PORT}")
    print("Endpoints:")
    print("  GET  /api/status                       - bridge health + booted sims")
    print("  GET  /api/simulators                   - list booted + available sims")
    print("  GET  /api/apps?udid=                   - installed apps on a sim")
    print("  GET  /api/screenshot?udid=             - capture screen as base64 PNG")
    print("  GET  /api/logs?lines=200               - tail console of launched app")
    print("  GET  /api/oslog?udid=&process=&minutes= - unified log (os_log/Logger)")
    print("  GET  /api/crashes?process=             - recent crash reports")
    print("  POST /api/boot      {udid}             - boot a simulator")
    print("  POST /api/launch    {udid, bundleId}   - (re)launch app, console attached")
    print("  POST /api/terminate {udid, bundleId}   - stop app")
    print("  POST /api/openurl   {udid, url}        - open a deep link / URL")
    print("  POST /api/install   {udid, path}       - install a built .app")
    print()
    print("Keep this running while using the iOS lane in Debug Map.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[sim-bridge] Stopped.")
        stop_console()
