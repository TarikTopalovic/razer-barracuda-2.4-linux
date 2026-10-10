#!/usr/bin/env python3
"""Razer Linux web UI — local server for the Viper Mini (OpenRazer kernel driver, sysfs)
and the Barracuda 2.4 (vendor HID frames from ../tools/razer_barracuda.py).
Serves index.html and a small JSON API on 127.0.0.1:3816. Stdlib only."""
import glob, json, os, re, struct, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import razer_barracuda as rb  # find_node, get_param, set_param, rf_refresh — only captured frames

PORT = 3816
LOCK = threading.Lock()   # one device op at a time (the dongle's HID channel wedges if hammered)


# ------------------------------------------------------------------ Viper Mini (sysfs)
def mouse_dir():
    for d in glob.glob("/sys/bus/hid/drivers/razermouse/*"):
        if os.path.exists(d + "/dpi"):
            return d


def rd(d, f, binary=False):
    try:
        with open(f"{d}/{f}", "rb") as fh:
            v = fh.read()
        return v if binary else v.decode().strip()
    except OSError:
        return None


def wr(d, f, data):
    with open(f"{d}/{f}", "wb") as fh:
        fh.write(data if isinstance(data, bytes) else str(data).encode())


def mouse_state():
    d = mouse_dir()
    if not d:
        return {"present": False}
    dpi = rd(d, "dpi")
    if dpi is None:  # files exist but unreadable -> not in the openrazer group yet
        return {"present": True, "access": False}
    st = rd(d, "dpi_stages", True) or b""
    stages = [struct.unpack(">HH", st[i:i + 4])[0] for i in range(1, len(st) - 3, 4)]
    return {"present": True, "access": True, "name": "Razer Viper Mini",
            "dpi": int(dpi.split(":")[0]), "stages": stages, "active": st[0] if st else None,
            "poll": int(rd(d, "poll_rate") or 0), "brightness": int(rd(d, "logo_led_brightness") or 0),
            "firmware": rd(d, "firmware_version"), "serial": rd(d, "device_serial")}


def rgb(h):
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", h or ""):
        raise ValueError("colour must be #rrggbb")
    return bytes.fromhex(h.lstrip("#"))


def mouse_set(b):
    d = mouse_dir()
    if not d:
        raise ValueError("Viper Mini not connected")
    if "dpi" in b:
        v = int(b["dpi"])
        if not 100 <= v <= 8500:
            raise ValueError("DPI must be 100-8500")
        wr(d, "dpi", struct.pack(">HH", v, v))
    if "stages" in b:
        s = [int(x) for x in b["stages"]]
        a = int(b.get("active", 1))
        if not 1 <= len(s) <= 5 or not all(100 <= x <= 8500 for x in s) or not 1 <= a <= len(s):
            raise ValueError("1-5 stages of 100-8500 DPI")
        wr(d, "dpi_stages", bytes([a]) + b"".join(struct.pack(">HH", x, x) for x in s))
    if "poll" in b:
        if int(b["poll"]) not in (125, 500, 1000):
            raise ValueError("poll rate must be 125, 500 or 1000")
        wr(d, "poll_rate", int(b["poll"]))
    fx = b.get("effect")
    files = {"static": "logo_matrix_effect_static", "breath": "logo_matrix_effect_breath",
             "spectrum": "logo_matrix_effect_spectrum", "off": "logo_matrix_effect_none"}
    if fx is not None and fx not in files:
        raise ValueError("unknown effect")
    # The mouse can answer BUSY and the driver drops the command without an error, so
    # lighting writes go out twice (idempotent). ponytail: read-back needs a raw HID report.
    for _ in range(2 if fx or "brightness" in b else 0):
        if fx:
            wr(d, files[fx], rgb(b.get("color")) if fx in ("static", "breath") else b"1")
        if "brightness" in b:
            wr(d, "logo_led_brightness", max(0, min(255, int(b["brightness"]))))
        time.sleep(0.05)


# ------------------------------------------------------------------ Barracuda 2.4 (hidraw + PipeWire)
def pa(*a):
    return subprocess.run(["pactl", *a], capture_output=True, text=True).stdout


def pa_dev(kind):  # Barracuda sink/source name, skipping monitors
    for l in pa("list", "short", kind).splitlines():
        n = l.split("\t")[1]
        if "Barracuda" in n and not n.endswith(".monitor"):
            return n


def head_open():
    node = rb.find_node()
    if not node:
        return None, "absent"
    try:
        return os.open(node, os.O_RDWR | os.O_NONBLOCK), None
    except PermissionError:
        return None, "denied"


def headset_state():
    sink, src = pa_dev("sinks"), pa_dev("sources")
    s = {"present": rb.find_node() is not None, "access": True}
    if sink:
        m = re.search(r"(\d+)%", pa("get-sink-volume", sink))
        s["volume"] = int(m.group(1)) if m else None
    if src:
        s["mic_muted"] = "yes" in pa("get-source-mute", src)
    fd, err = head_open()
    if err:
        s["access"] = err != "denied"
        return s
    try:
        for i in range(4):  # RF stalls while audio streams; retry refresh->GET (see razer_barracuda.cmd_battery)
            rb.rf_refresh(fd)
            time.sleep(0.05 * (i + 1))
            batt = rb.get_param(fd, 0x21)
            if batt is not None:
                s["battery"], s["charging"] = batt, bool(rb.get_param(fd, 0x2a))
                break
    finally:
        os.close(fd)
    return s


def headset_set(b):
    if "volume" in b:
        pa("set-sink-volume", pa_dev("sinks") or "", f"{max(0, min(100, int(b['volume'])))}%")
    if "mic_muted" in b:
        pa("set-source-mute", pa_dev("sources") or "", "1" if b["mic_muted"] else "0")
    # No EQ here: over the 2.4 dongle 0x93 does nothing (Synapse EQ is PC-side DSP, FINDINGS.txt).
    hid = {k: b[k] for k in ("sidetone", "sidetone_level", "power_saving") if k in b}
    if not hid:
        return
    fd, err = head_open()
    if err:
        raise ValueError("headset dongle not found" if err == "absent" else "no access to the dongle (udev rule)")
    try:
        if "sidetone" in hid:
            rb.set_param(fd, 0x98, 1 if hid["sidetone"] else 0)
            time.sleep(0.02)
        if "sidetone_level" in hid:
            rb.set_param(fd, 0x99, max(0, min(16, int(hid["sidetone_level"]))))
        if "power_saving" in hid:
            rb.set_param(fd, 0xac, max(0, min(255, int(hid["power_saving"]))))
    finally:
        os.close(fd)


# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html")) as f:  # fill this checkout's path into the setup hint
                page = f.read().replace("{{RULE}}", os.path.join(os.path.dirname(HERE), "tools", "99-razer-barracuda.rules"))
                return self.send(200, page.replace("{{USER}}", os.environ.get("USER", "$USER")).encode(), "text/html; charset=utf-8")
        if self.path == "/api/state":
            with LOCK:
                return self.send(200, {"mouse": mouse_state(), "headset": headset_state()})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        # Only same-origin pages may change devices (blocks other websites POSTing to localhost).
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send(403, {"error": "forbidden origin"})
        fn = {"/api/mouse": mouse_set, "/api/headset": headset_set}.get(self.path)
        if not fn:
            return self.send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
            with LOCK:
                fn(body)
            self.send(200, {"ok": True})
        except PermissionError:
            self.send(403, {"error": "no access to the device — run the one-time setup (see README)"})
        except (ValueError, TypeError, OSError) as e:
            self.send(400, {"error": str(e)})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
