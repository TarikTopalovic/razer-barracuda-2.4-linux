#!/usr/bin/env python3
"""
Razer Barracuda 2.4 — Synapse-style control panel (PyQt6).

Sidebar navigation + content panels, Razer dark theme. Talks the vendor PI/PA
HID protocol over hidraw for battery/sidetone/power-saving, and drives a
PipeWire filter-chain (node "barracuda_eq") for a live, no-lag equalizer.
"""
import glob
import json
import os
import re
import select
import subprocess
import sys
import time

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty, QRectF, QEasingCurve

VID_PID = "00001532:0000053C"
GREEN = "#44d62c"
GREEN_Q = QtGui.QColor(0x44, 0xd6, 0x2c)
INK = "#0a0a0b"           # window
SIDE = "#0e0f10"          # sidebar
PANEL = "#161719"         # content panel
CARD = "#1c1e21"          # cards
CARD2 = "#202327"
BRD = "#2a2e33"
TXT = "#f3f4f5"
SUB = "#8b9298"
FONT = "Fira Sans"
FONT_D = "Fira Sans Condensed"
EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_LABELS = ["31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]
# Tuned to the Barracuda 2.4's signature (bass bump ~100Hz, low-mid dip ~300-400Hz).
# Bands: 31 63 125 250 500 1k 2k 4k 8k 16k
EQ_PRESETS = {
    "Flat":    [0,  0,  0,  0,  0,  0,  0,  0,  0,  0],   # raw headset, no EQ
    "Game":    [1,  0, -3, -1,  1,  2,  4,  5,  3,  1],   # tame boom, footsteps + air
    "FPS":     [-3, -4, -4, -2, 0,  2,  5,  6,  4,  2],   # competitive: kill boom, max steps
    "Music":   [0, -1, -2,  2,  2,  1,  1,  1,  1,  0],   # fix low-mid dip, clean balance
    "Rock":    [3,  2,  0,  1,  2,  2,  2,  2,  1,  0],   # tight bass, present guitars
    "Hip-Hop": [7,  7,  4,  1,  0,  1,  2,  1,  0,  0],   # deep bass + vocal presence
    "EDM":     [9,  8,  5,  1, -1,  0,  1,  2,  3,  3],   # club bass + crisp highs
    "Bass":    [8,  7,  4,  1,  0,  0,  0,  0,  1,  1],   # deep sub-bass for fun
    "Movie":   [5,  4,  1,  0,  1,  2,  2,  1,  3,  3],   # rumble + dialogue + effects
    "Warm":    [3,  3,  2,  1,  0,  0, -1, -2, -2, -2],   # smooth, cozy, less harsh
    "Bright":  [-2, -2, -1, 0,  1,  2,  3,  4,  5,  4],   # detail + sparkle, less bass
    "Vocal":   [-4, -4, -2, 1,  3,  4,  3,  1,  0, -1],   # voice/podcast/call clarity
}


# ----------------------------------------------------------------- audio helpers
def _run(*a):
    try:
        return subprocess.run(list(a), capture_output=True, text=True).stdout
    except OSError:
        return ""


def mic_source():
    for l in _run("pactl", "list", "short", "sources").splitlines():
        if "barracuda" in l.lower() and "monitor" not in l.lower():
            return l.split("\t")[1]
    return None


def mic_muted():
    s = mic_source()
    return ("yes" in _run("pactl", "get-source-mute", s).lower()) if s else None


def mic_set_mute(m):
    s = mic_source()
    if s:
        subprocess.run(["pactl", "set-source-mute", s, "1" if m else "0"])


def _eq_sink():
    for l in _run("pactl", "list", "short", "sinks").splitlines():
        if "barracuda" in l.lower() and "monitor" not in l.lower():
            return l.split("\t")[1]
    return None


def sidetone_module():
    out = _run("pactl", "list", "modules")
    cur = None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Module #"):
            cur = s.split("#")[1]
        elif cur and "barracuda" in s.lower() and "mono-fallback" in s.lower() and "loopback" in out.lower():
            return cur
    for l in _run("pactl", "list", "short", "modules").splitlines():
        if "module-loopback" in l and "barracuda" in l.lower():
            return l.split("\t")[0]
    return None


def sidetone_off():
    for l in _run("pactl", "list", "short", "modules").splitlines():
        if "module-loopback" in l and "barracuda" in l.lower():
            subprocess.run(["pactl", "unload-module", l.split("\t")[0]])


def sidetone_on(level):
    mic, sink = mic_source(), _eq_sink()
    if not (mic and sink):
        return False
    sidetone_off()
    subprocess.run(["pactl", "load-module", "module-loopback", f"source={mic}",
                    f"sink={sink}", "latency_msec=15", "source_dont_move=true"],
                   capture_output=True, text=True)
    return True


# ----------------------------------------------------------------- EQ (PipeWire)
EQ_CONF = os.path.expanduser("~/.config/pipewire/pipewire.conf.d/99-barracuda-eq.conf")


def eq_node():
    try:
        for o in json.loads(_run("pw-dump")):
            if o.get("info", {}).get("props", {}).get("node.name") == "barracuda_eq":
                return str(o["id"])
    except Exception:
        pass
    return None


def eq_apply(gains):
    nid = eq_node()
    if not nid:
        return False
    params = " ".join(f'"eq{i}:Gain" {float(g)}' for i, g in enumerate(gains))
    subprocess.run(["pw-cli", "set-param", nid, "Props", "{ params = [ %s ] }" % params],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        txt = open(EQ_CONF).read()
        for i, g in enumerate(gains):
            txt = re.sub(rf'(name = eq{i} control = \{{[^}}]*?"Gain" = )[-0-9.]+',
                         lambda m, gg=g: m.group(1) + str(float(gg)), txt)
        open(EQ_CONF, "w").write(txt)
    except OSError:
        pass
    return True


def eq_current():
    g = [0] * 10
    try:
        txt = open(EQ_CONF).read()
        for i in range(10):
            m = re.search(rf'name = eq{i} control = \{{[^}}]*?"Gain" = ([-0-9.]+)', txt)
            if m:
                g[i] = int(round(float(m.group(1))))
    except OSError:
        pass
    return g


# ----------------------------------------------------------------- user profiles
PROFILES_PATH = os.path.expanduser("~/.config/barracuda-eq/profiles.json")


def load_profiles():
    try:
        d = json.load(open(PROFILES_PATH))
        return dict(d.get("custom", {})), list(d.get("favorites", []))
    except Exception:
        return {}, []


def save_profiles(custom, favorites):
    try:
        os.makedirs(os.path.dirname(PROFILES_PATH), exist_ok=True)
        json.dump({"custom": custom, "favorites": favorites},
                  open(PROFILES_PATH, "w"), indent=2)
    except OSError:
        pass


# ============================================================ protocol layer
class Barracuda:
    def __init__(self):
        self.fd = None
        self.open()

    def _node(self):
        for h in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            try:
                if VID_PID in open(os.path.join(h, "device/uevent")).read().upper():
                    return "/dev/" + os.path.basename(h)
            except OSError:
                pass
        return None

    def open(self):
        n = self._node()
        try:
            self.fd = os.open(n, os.O_RDWR | os.O_NONBLOCK) if n else None
        except OSError:
            self.fd = None
        return self.fd is not None

    @property
    def present(self):
        return self.fd is not None

    def _drain(self):
        while self.fd and select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 256)
            except OSError:
                return

    def _frame(self, sub, param, length, data=b""):
        b = bytearray(64)
        b[0:9] = bytes([0x01, 0x80, length, 0x50, 0x41, 0x08, 0x08, sub, param])
        for i, v in enumerate(data):
            b[9 + i] = v
        return bytes(b)

    def _read(self, param, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not select.select([self.fd], [], [], 0.03)[0]:
                continue
            try:
                d = os.read(self.fd, 256)
            except OSError:
                break
            if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49 and d[13] == param:
                return d
        return None

    def get(self, param, timeout=0.35):
        if not self.fd:
            return None
        self._drain()
        try:
            os.write(self.fd, self._frame(0x03, param, 0x08))
        except OSError:
            self.open(); return None
        d = self._read(param, timeout)
        return d[16] if d else None

    def get_string(self, param, timeout=0.35):
        if not self.fd:
            return None
        self._drain()
        try:
            os.write(self.fd, self._frame(0x03, param, 0x08))
        except OSError:
            self.open(); return None
        d = self._read(param, timeout)
        if not d:
            return None
        return (bytes(c for c in d[16:32] if 32 <= c < 127).decode("ascii", "ignore").strip() or None)

    def set(self, param, value):
        if not self.fd:
            return
        try:
            os.write(self.fd, self._frame(0x04, param, 0x09, bytes([0x00, 0x01, value & 0xff])))
        except OSError:
            self.open()

    def refresh_telemetry(self):
        if not self.fd:
            return
        b = bytearray(64)
        b[0:10] = bytes([0x01, 0x80, 0x07, 0x50, 0x41, 0x0e, 0x08, 0x02, 0xe1, 0x01])
        try:
            os.write(self.fd, bytes(b))
        except OSError:
            self.open()


# ============================================================ widgets
class Toggle(QtWidgets.QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._p = 3.0
        self._a = QPropertyAnimation(self, b"knob", self); self._a.setDuration(150)
        self._a.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(lambda on: (self._a.setEndValue(self.width() - 23 if on else 3.0), self._a.start()))

    @pyqtProperty(float)
    def knob(self):
        return self._p

    @knob.setter
    def knob(self, v):
        self._p = v; self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GREEN_Q if self.isChecked() else QtGui.QColor("#2b2f34"))
        p.drawRoundedRect(0, 0, self.width(), self.height(), 13, 13)
        p.setBrush(QtGui.QColor("#fff"))
        p.drawEllipse(QRectF(self._p, 3, 20, 20))


class NavButton(QtWidgets.QAbstractButton):
    def __init__(self, glyph, text):
        super().__init__()
        self.glyph, self.text = glyph, text
        self.setCheckable(True)
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        if on:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QtGui.QColor(0x44, 0xd6, 0x2c, 28))
            p.drawRoundedRect(8, 4, self.width() - 16, self.height() - 8, 8, 8)
            p.setBrush(GREEN_Q); p.drawRoundedRect(8, 10, 3, self.height() - 20, 1, 1)
        p.setPen(QtGui.QColor(GREEN if on else SUB))
        p.setFont(QtGui.QFont("Fira Sans", 15))
        p.drawText(QRectF(20, 0, 30, self.height()), Qt.AlignmentFlag.AlignVCenter, self.glyph)
        p.setPen(QtGui.QColor(TXT if on else SUB))
        f = QtGui.QFont("Fira Sans", 11); f.setWeight(QtGui.QFont.Weight.DemiBold if on else QtGui.QFont.Weight.Normal)
        p.setFont(f)
        p.drawText(QRectF(50, 0, self.width() - 56, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)


class BatteryBar(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.pct = None

    def set(self, p):
        self.pct = p; self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self); p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor("#23262a")); p.drawRoundedRect(0, 0, self.width(), 8, 4, 4)
        if self.pct:
            col = GREEN_Q if self.pct > 20 else QtGui.QColor("#ff4d4d")
            p.setBrush(col); p.drawRoundedRect(0, 0, int(self.width() * self.pct / 100), 8, 4, 4)


class Card(QtWidgets.QFrame):
    def __init__(self, title=None):
        super().__init__()
        self.setObjectName("card")
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(22, 20, 22, 20); self.v.setSpacing(16)
        if title:
            t = QtWidgets.QLabel(title); t.setObjectName("ctitle"); self.v.addWidget(t)


class Row(QtWidgets.QWidget):
    def __init__(self, label, desc, widget):
        super().__init__()
        h = QtWidgets.QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0)
        col = QtWidgets.QVBoxLayout(); col.setSpacing(2)
        a = QtWidgets.QLabel(label); a.setObjectName("rlab")
        b = QtWidgets.QLabel(desc); b.setObjectName("rdesc")
        col.addWidget(a); col.addWidget(b)
        h.addLayout(col); h.addStretch(); h.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)


class EQBand(QtWidgets.QWidget):
    def __init__(self, label, cb):
        super().__init__()
        v = QtWidgets.QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self.val = QtWidgets.QLabel("0"); self.val.setObjectName("eqval")
        self.val.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.s = QtWidgets.QSlider(Qt.Orientation.Vertical)
        self.s.setRange(-12, 12); self.s.setValue(0); self.s.setFixedHeight(150)
        self.s.valueChanged.connect(lambda x: self.val.setText(f"{x:+d}" if x else "0"))
        self.s.sliderReleased.connect(cb)
        cap = QtWidgets.QLabel(label); cap.setObjectName("eqcap"); cap.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.val)
        v.addWidget(self.s, alignment=Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(cap)


# ============================================================ main window
class Panel(QtWidgets.QMainWindow):
    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.setWindowTitle("Razer Barracuda 2.4")
        self.setFixedSize(880, 600)
        self._model = None
        self.custom, self.favs = load_profiles()
        self._build()
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(4000)
        QtCore.QTimer.singleShot(120, self.refresh)

    def _build(self):
        c = QtWidgets.QWidget(); self.setCentralWidget(c)
        h = QtWidgets.QHBoxLayout(c); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(self._sidebar())
        self.stack = QtWidgets.QStackedWidget(); self.stack.setObjectName("content")
        h.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_sound())
        self.stack.addWidget(self._page_mic())
        self.stack.addWidget(self._page_power())
        self.stack.addWidget(self._page_device())
        self.navs[0].setChecked(True)
        self.setStyleSheet(STYLE)

    # ---- sidebar
    def _sidebar(self):
        s = QtWidgets.QFrame(); s.setObjectName("side"); s.setFixedWidth(232)
        v = QtWidgets.QVBoxLayout(s); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        # brand
        brand = QtWidgets.QLabel("RAZER"); brand.setObjectName("brand")
        bf = brand.font(); bf.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 6); brand.setFont(bf)
        brand.setContentsMargins(24, 22, 0, 18)
        v.addWidget(brand)
        # device card
        dc = QtWidgets.QFrame(); dc.setObjectName("devcard")
        dl = QtWidgets.QVBoxLayout(dc); dl.setContentsMargins(18, 16, 18, 16); dl.setSpacing(8)
        top = QtWidgets.QHBoxLayout()
        ic = QtWidgets.QLabel("🎧"); ic.setObjectName("devicon")
        nm = QtWidgets.QLabel("Barracuda 2.4"); nm.setObjectName("devname")
        top.addWidget(ic); top.addSpacing(4); top.addWidget(nm); top.addStretch()
        dl.addLayout(top)
        self.bat = BatteryBar(); dl.addWidget(self.bat)
        self.batlbl = QtWidgets.QLabel("—"); self.batlbl.setObjectName("batlbl")
        dl.addWidget(self.batlbl)
        v.addWidget(dc)
        v.addSpacing(14)
        # nav
        self.navs = []
        grp = QtWidgets.QButtonGroup(self)
        for i, (g, t) in enumerate([("♪", "SOUND"), ("🎙", "MICROPHONE"), ("⚡", "POWER"), ("ⓘ", "DEVICE")]):
            nb = NavButton(g, t); grp.addButton(nb)
            nb.clicked.connect(lambda _, idx=i: self.stack.setCurrentIndex(idx))
            self.navs.append(nb); v.addWidget(nb)
        v.addStretch()
        ft = QtWidgets.QLabel("Unofficial · Linux"); ft.setObjectName("foot"); ft.setContentsMargins(24, 0, 0, 16)
        v.addWidget(ft)
        return s

    def _page(self, title, sub):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(34, 30, 34, 30); v.setSpacing(20)
        head = QtWidgets.QVBoxLayout(); head.setSpacing(3)
        t = QtWidgets.QLabel(title); t.setObjectName("h1")
        s = QtWidgets.QLabel(sub); s.setObjectName("h1sub")
        head.addWidget(t); head.addWidget(s)
        v.addLayout(head)
        return w, v

    # ---- SOUND (EQ)
    def _page_sound(self):
        w, v = self._page("Sound", "Equalizer — applied live to your headphones")
        card = Card()
        # favourites quick-row
        self.fav_host = QtWidgets.QWidget()
        self.fav_row = QtWidgets.QHBoxLayout(self.fav_host)
        self.fav_row.setContentsMargins(0, 0, 0, 0); self.fav_row.setSpacing(8)
        card.v.addWidget(self.fav_host)
        # picker: dropdown + favourite + delete
        pick = QtWidgets.QHBoxLayout(); pick.setSpacing(8)
        self.combo = QtWidgets.QComboBox(); self.combo.setObjectName("combo"); self.combo.setFixedHeight(38)
        self.combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.combo.activated.connect(self._combo_pick)
        self.fav_btn = QtWidgets.QPushButton("☆"); self.fav_btn.setObjectName("iconbtn"); self.fav_btn.setFixedSize(38, 38)
        self.fav_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.fav_btn.clicked.connect(self._toggle_fav_current)
        self.del_btn = QtWidgets.QPushButton("🗑"); self.del_btn.setObjectName("iconbtn"); self.del_btn.setFixedSize(38, 38)
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.del_btn.clicked.connect(self._delete_current)
        pick.addWidget(self.combo, 1); pick.addWidget(self.fav_btn); pick.addWidget(self.del_btn)
        card.v.addLayout(pick)
        # bands
        bands = QtWidgets.QHBoxLayout(); bands.setSpacing(10)
        self.eq_bands = []
        for lbl in EQ_LABELS:
            band = EQBand(lbl, self.apply_eq); self.eq_bands.append(band); bands.addWidget(band)
        card.v.addLayout(bands)
        # save new
        save = QtWidgets.QHBoxLayout(); save.setSpacing(8)
        self.save_name = QtWidgets.QLineEdit(); self.save_name.setObjectName("nameinput")
        self.save_name.setPlaceholderText("Name a new profile from these sliders…"); self.save_name.setFixedHeight(38)
        self.save_name.returnPressed.connect(self.save_current)
        sb = QtWidgets.QPushButton("＋  Save"); sb.setObjectName("savebtn")
        sb.setCursor(Qt.CursorShape.PointingHandCursor); sb.clicked.connect(self.save_current)
        save.addWidget(self.save_name, 1); save.addWidget(sb)
        card.v.addLayout(save)
        v.addWidget(card); v.addStretch()
        self._current = "Flat"
        self._rebuild_profiles()
        for b, g in zip(self.eq_bands, eq_current()):
            b.s.blockSignals(True); b.s.setValue(g); b.val.setText(f"{g:+d}" if g else "0"); b.s.blockSignals(False)
        return w

    def _all_profiles(self):
        d = dict(EQ_PRESETS); d.update(self.custom); return d

    def _rebuild_profiles(self):
        while self.fav_row.count():
            it = self.fav_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        favs = [n for n in self.favs if n in self._all_profiles()]
        if favs:
            for name in favs:
                b = QtWidgets.QPushButton("★ " + name); b.setObjectName("favchip")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _, n=name: self._pick(n))
                self.fav_row.addWidget(b)
        else:
            lab = QtWidgets.QLabel("☆  No favourites yet — pick a profile, tap the star")
            lab.setObjectName("rdesc"); self.fav_row.addWidget(lab)
        self.fav_row.addStretch()
        self.combo.blockSignals(True); self.combo.clear()
        names = list(self._all_profiles().keys())
        for n in names:
            self.combo.addItem(("★ " if n in self.favs else "") + n, n)
        self.combo.setCurrentIndex(names.index(self._current) if self._current in names else 0)
        self.combo.blockSignals(False)
        self.fav_btn.setText("★" if self._current in self.favs else "☆")
        self.del_btn.setEnabled(self._current in self.custom)

    def _combo_pick(self, idx):
        name = self.combo.itemData(idx)
        if name:
            self._pick(name)

    def _pick(self, name):
        self._current = name
        self.load_preset(name)
        self._rebuild_profiles()

    def _toggle_fav_current(self):
        n = self._current
        (self.favs.remove(n) if n in self.favs else self.favs.append(n))
        save_profiles(self.custom, self.favs)
        self._rebuild_profiles()

    def _delete_current(self):
        n = self._current
        if n in self.custom:
            self.custom.pop(n, None)
            if n in self.favs:
                self.favs.remove(n)
            self._current = "Flat"
            save_profiles(self.custom, self.favs)
            self._rebuild_profiles()

    def save_current(self):
        gains = [b.s.value() for b in self.eq_bands]
        name = self.save_name.text().strip()
        if not name:
            k = 1
            while f"Custom {k}" in self._all_profiles():
                k += 1
            name = f"Custom {k}"
        self.custom[name] = gains
        self._current = name
        save_profiles(self.custom, self.favs)
        self.save_name.clear()
        self._rebuild_profiles()

    # ---- MIC
    def _page_mic(self):
        w, v = self._page("Microphone", "Sidetone and mute")
        card = Card()
        self.st_on = Toggle(); self.st_on.toggled.connect(self.apply_sidetone)
        card.v.addWidget(Row("Sidetone", "Hear your own mic in the headset", self.st_on))
        slrow = QtWidgets.QHBoxLayout()
        self.st_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal); self.st_slider.setRange(0, 16); self.st_slider.setValue(9)
        self.st_badge = QtWidgets.QLabel("9"); self.st_badge.setObjectName("badge"); self.st_badge.setFixedWidth(32)
        self.st_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.st_slider.valueChanged.connect(lambda x: self.st_badge.setText(str(x)))
        self.st_slider.sliderReleased.connect(self.apply_sidetone)
        lv = QtWidgets.QLabel("Level"); lv.setObjectName("rlab")
        slrow.addWidget(lv); slrow.addWidget(self.st_slider); slrow.addWidget(self.st_badge)
        card.v.addLayout(slrow)
        self.mic_tog = Toggle(); self.mic_tog.toggled.connect(lambda on: mic_set_mute(on))
        card.v.addWidget(Row("Mute microphone", "Silence the mic (system mixer)", self.mic_tog))
        v.addWidget(card); v.addStretch()
        return w

    # ---- POWER
    def _page_power(self):
        w, v = self._page("Power", "Battery and auto-off")
        card = Card()
        seg = QtWidgets.QHBoxLayout(); seg.setSpacing(0)
        self.ps_grp = QtWidgets.QButtonGroup(self)
        for i, (lbl, m) in enumerate([("Off", 0), ("15m", 15), ("30m", 30), ("45m", 45), ("60m", 60)]):
            b = QtWidgets.QPushButton(lbl); b.setObjectName("seg"); b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor); b.setProperty("mins", m)
            if i == 0:
                b.setChecked(True)
            b.clicked.connect(self.apply_power); self.ps_grp.addButton(b); seg.addWidget(b)
        lab = QtWidgets.QLabel("Auto-off on battery after inactivity"); lab.setObjectName("rdesc")
        card.v.addWidget(lab)
        card.v.addLayout(seg)
        v.addWidget(card); v.addStretch()
        return w

    # ---- DEVICE
    def _page_device(self):
        w, v = self._page("Device", "Status and information")
        card = Card()
        self.i_model = self._inforow(card, "Model")
        self.i_fw = self._inforow(card, "Firmware")
        self.i_conn = self._inforow(card, "Connection")
        self.i_batt = self._inforow(card, "Battery")
        self.i_chg = self._inforow(card, "Charging")
        self.i_mic = self._inforow(card, "Microphone")
        v.addWidget(card); v.addStretch()
        return w

    def _inforow(self, card, label):
        r = QtWidgets.QHBoxLayout()
        a = QtWidgets.QLabel(label); a.setObjectName("rdesc")
        b = QtWidgets.QLabel("…"); b.setObjectName("rval")
        r.addWidget(a); r.addStretch(); r.addWidget(b)
        card.v.addLayout(r)
        return b

    # ---- actions
    def apply_eq(self, *_):
        eq_apply([bd.s.value() for bd in self.eq_bands])

    def load_preset(self, name):
        gains = self._all_profiles().get(name, [0] * 10)
        for bd, g in zip(self.eq_bands, gains):
            bd.s.blockSignals(True); bd.s.setValue(g); bd.val.setText(f"{g:+d}" if g else "0"); bd.s.blockSignals(False)
        eq_apply(gains)

    def apply_sidetone(self, *_):
        if self.st_on.isChecked():
            if sidetone_on(self.st_slider.value()):
                self._toast(f"Sidetone on · {self.st_slider.value()}")
            else:
                self.st_on.blockSignals(True); self.st_on.setChecked(False); self.st_on.blockSignals(False)
        else:
            sidetone_off()

    def apply_power(self):
        m = self.ps_grp.checkedButton().property("mins")
        self.dev.set(0xac, m)

    def _toast(self, msg):
        pass

    # ---- refresh
    def refresh(self):
        if not self.dev.present:
            self.dev.open()
        link = self.dev.get(0x20)
        self.dev.refresh_telemetry()
        batt = self.dev.get(0x21); chg = self.dev.get(0x2a)
        ok = batt is not None and 0 <= batt <= 100
        self.bat.set(batt if ok else 0)
        self.batlbl.setText((f"{batt}%  " + ("charging" if chg else "")) if ok
                            else ("connected" if link is not None else "not on 2.4"))
        if self._model is None:
            self._model = self.dev.get_string(0x00) or "Barracuda 2.4"
        self.i_model.setText(self._model)
        self.i_fw.setText("v1.0")
        self.i_conn.setText("2.4 GHz" if link is not None else "not linked")
        self.i_batt.setText(f"{batt}%" if ok else "—")
        self.i_chg.setText("yes" if chg else ("no" if chg is not None else "—"))
        mm = mic_muted()
        self.i_mic.setText("muted" if mm else ("active" if mm is not None else "—"))
        st = sidetone_module() is not None
        if self.st_on.isChecked() != st:
            self.st_on.blockSignals(True); self.st_on.setChecked(st); self.st_on.blockSignals(False)


STYLE = f"""
* {{ font-family:'{FONT}','Cantarell',sans-serif; color:{TXT}; outline:0; }}
QMainWindow {{ background:{INK}; }}
QLabel {{ background:transparent; font-size:13px; }}
#side {{ background:{SIDE}; border-right:1px solid #1a1c1f; }}
#brand {{ color:{GREEN}; font-size:13px; font-weight:800; }}
#devcard {{ background:{CARD}; border:1px solid {BRD}; border-radius:12px; margin:0 16px; }}
#devicon {{ font-size:18px; }}
#devname {{ font-size:14px; font-weight:700; }}
#batlbl {{ color:{SUB}; font-size:11px; font-weight:600; }}
#foot {{ color:#4a5056; font-size:10px; }}
#content {{ background:{INK}; }}
#h1 {{ font-family:'{FONT_D}'; font-size:27px; font-weight:900; }}
#h1sub {{ color:{SUB}; font-size:12px; }}
#card {{ background:{PANEL}; border:1px solid {BRD}; border-radius:16px; }}
#ctitle {{ color:{GREEN}; font-size:10px; font-weight:800; letter-spacing:2px; }}
#rlab {{ font-size:14px; font-weight:600; }}
#rdesc {{ color:{SUB}; font-size:12px; }}
#rval {{ font-size:13px; font-weight:700; }}
#eqcap {{ color:{SUB}; font-size:10px; font-weight:600; }}
#eqval {{ color:{GREEN}; font-size:11px; font-weight:700; }}
#badge {{ background:#10240b; color:{GREEN}; border:1px solid #245417; border-radius:8px; padding:3px 0; font-weight:800; }}
QPushButton#chip {{ background:{CARD}; border:1px solid {BRD}; border-radius:16px; padding:8px 18px; font-size:12px; font-weight:600; color:#c6cdd2; }}
QPushButton#chip:checked {{ background:{GREEN}; color:#06210a; border-color:{GREEN}; font-weight:700; }}
QPushButton#chip:hover:!checked {{ border-color:#3a4147; color:{TXT}; }}
QLineEdit#nameinput {{ background:{CARD}; border:1px solid {BRD}; border-radius:9px;
    padding:0 12px; font-size:13px; color:{TXT}; selection-background-color:{GREEN}; }}
QLineEdit#nameinput:focus {{ border-color:{GREEN}; }}
QPushButton#savebtn {{ background:{GREEN}; color:#06210a; border:none; border-radius:9px;
    padding:0 20px; font-weight:700; font-size:13px; min-height:36px; }}
QPushButton#savebtn:hover {{ background:#54e63a; }}
QMenu {{ background:{CARD2}; border:1px solid {BRD}; border-radius:9px; padding:5px; }}
QMenu::item {{ padding:8px 18px; border-radius:6px; font-size:12px; color:{TXT}; }}
QMenu::item:selected {{ background:{GREEN}; color:#06210a; }}
QPushButton#favchip {{ background:{GREEN}; color:#06210a; border:none; border-radius:15px;
    padding:7px 16px; font-size:12px; font-weight:700; }}
QPushButton#favchip:hover {{ background:#54e63a; }}
QComboBox#combo {{ background:{CARD}; border:1px solid {BRD}; border-radius:9px; padding:0 14px;
    font-size:13px; font-weight:600; color:{TXT}; }}
QComboBox#combo:hover {{ border-color:#3a4147; }}
QComboBox#combo::drop-down {{ border:none; width:26px; }}
QComboBox#combo::down-arrow {{ image:none; border-left:5px solid transparent;
    border-right:5px solid transparent; border-top:6px solid {SUB}; margin-right:9px; }}
QComboBox QAbstractItemView {{ background:{CARD2}; border:1px solid {BRD}; border-radius:8px;
    padding:4px; selection-background-color:{GREEN}; selection-color:#06210a; outline:0; }}
QComboBox QAbstractItemView::item {{ padding:7px 10px; border-radius:5px; min-height:22px; }}
QPushButton#iconbtn {{ background:{CARD}; border:1px solid {BRD}; border-radius:9px; font-size:15px; }}
QPushButton#iconbtn:hover {{ border-color:{GREEN}; }}
QPushButton#iconbtn:disabled {{ color:#3a4147; }}
QPushButton#seg {{ background:{CARD}; border:1px solid {BRD}; padding:11px 0; font-size:12px; font-weight:700; color:#c6cdd2; min-width:74px; }}
QPushButton#seg:first {{ border-top-left-radius:10px; border-bottom-left-radius:10px; }}
QPushButton#seg:last {{ border-top-right-radius:10px; border-bottom-right-radius:10px; }}
QPushButton#seg:checked {{ background:{GREEN}; color:#06210a; border-color:{GREEN}; }}
QPushButton#seg:hover:!checked {{ background:{CARD2}; color:{TXT}; }}
QSlider::groove:horizontal {{ height:6px; background:#23262a; border-radius:3px; }}
QSlider::handle:horizontal {{ width:18px; height:18px; margin:-7px 0; border-radius:9px; background:#fff; border:2px solid {GREEN}; }}
QSlider::sub-page:horizontal {{ background:{GREEN}; border-radius:3px; }}
QSlider::groove:vertical {{ width:6px; background:#23262a; border-radius:3px; }}
QSlider::handle:vertical {{ height:16px; width:16px; margin:0 -6px; border-radius:8px; background:#fff; border:2px solid {GREEN}; }}
QSlider::add-page:vertical {{ background:qlineargradient(x1:0,y1:1,x2:0,y2:0, stop:0 #2f7d22, stop:1 {GREEN}); border-radius:3px; }}
"""


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Razer Barracuda 2.4")
    dev = Barracuda()
    if not dev.present:
        QtWidgets.QMessageBox.critical(None, "Razer Barracuda 2.4",
            "Device not found / no hidraw access. Plug in the dongle; install the udev rule.")
        return 1
    win = Panel(dev); win.show()
    return app.exec()


if __name__ == "__main__":
    main()
