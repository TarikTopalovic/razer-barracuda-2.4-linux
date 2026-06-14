#!/usr/bin/env python3
"""
Razer Barracuda 2.4 — control panel (PyQt6), polished dark UI.

Uses the reverse-engineered vendor (PI/PA) HID protocol over raw hidraw to expose
every dongle-controllable feature: battery/charging read, connection state,
sidetone, and power-saving — all verified over the 2.4 dongle using the exact
frames Synapse itself sends. Audio EQ / THX Spatial are PC-side DSP (not headset
commands over 2.4), so EQ is delegated to EasyEffects. Only exact captured
frames are sent — no opcode guessing.
"""
import glob
import json
import os
import select
import shutil
import subprocess
import sys
import time

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty, QRectF, QPointF, QEasingCurve

VID_PID = "00001532:0000053C"
# NOTE: the headset's built-in EQ (vendor param 0x93) is reachable over Bluetooth
# only. Over the 2.4 dongle a SET 0x93 is ACK'd but ignored (verified) — Synapse
# applies EQ as PC-side audio DSP, not a headset command. So this dongle GUI does
# EQ via EasyEffects (software) instead of pretending 0x93 works here.
GREEN = "#44d62c"
GREEN_Q = QtGui.QColor(0x44, 0xd6, 0x2c)
BG = "#08090a"
CARD = "#131517"
CARD_BRD = "#1f2326"
TXT = "#eef0f1"
SUB = "#7d858b"


def pgrep(name):
    return subprocess.run(["pgrep", "-x", name],
                          stdout=subprocess.DEVNULL).returncode == 0


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

    def get(self, param, timeout=0.35):
        if not self.fd:
            return None
        self._drain()
        try:
            os.write(self.fd, self._frame(0x03, param, 0x08))
        except OSError:
            self.open()
            return None
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not select.select([self.fd], [], [], 0.03)[0]:
                continue
            try:
                d = os.read(self.fd, 256)
            except OSError:
                break
            if len(d) >= 17 and d[3] == 0x50 and d[4] == 0x49 and d[13] == param:
                return d[16]
        return None

    def set(self, param, value):
        if not self.fd:
            return
        try:
            os.write(self.fd, self._frame(0x04, param, 0x09, bytes([0x00, 0x01, value & 0xff])))
        except OSError:
            self.open()

    def refresh_telemetry(self):
        """Poke the dongle to pull fresh battery/charging from the headset over
        2.4 (class 0x0e / param 0xe1). Without this a cold dongle returns no
        battery value — Synapse sends this before each battery poll."""
        if not self.fd:
            return
        b = bytearray(64)
        b[0:10] = bytes([0x01, 0x80, 0x07, 0x50, 0x41, 0x0e, 0x08, 0x02, 0xe1, 0x01])
        try:
            os.write(self.fd, bytes(b))
        except OSError:
            self.open()


# ============================================================ custom widgets
class Toggle(QtWidgets.QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(50, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 3.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    @pyqtProperty(float)
    def knob(self):
        return self._pos

    @knob.setter
    def knob(self, v):
        self._pos = v
        self.update()

    def _animate(self, on):
        self._anim.stop()
        self._anim.setEndValue(self.width() - 25 if on else 3.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track = GREEN_Q if self.isChecked() else QtGui.QColor("#2a2f33")
        p.setBrush(track)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 14, 14)
        p.setBrush(QtGui.QColor("#ffffff"))
        p.drawEllipse(QRectF(self._pos, 3, 22, 22))


class BatteryRing(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(184, 184)
        self.pct = None
        self.charging = False

    def set_value(self, pct, charging):
        self.pct, self.charging = pct, charging
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = QRectF(14, 14, 156, 156)
        # track
        pen = QtGui.QPen(QtGui.QColor("#1c2024"), 14, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(r, 0, 360 * 16)
        # progress
        if self.pct is not None:
            grad = QtGui.QConicalGradient(r.center(), 90)
            grad.setColorAt(0.0, GREEN_Q)
            grad.setColorAt(1.0, QtGui.QColor("#0f8a18"))
            pen2 = QtGui.QPen(QtGui.QBrush(grad), 14, cap=Qt.PenCapStyle.RoundCap)
            p.setPen(pen2)
            p.drawArc(r, 90 * 16, -int(360 * 16 * (self.pct / 100.0)))
        # center text
        p.setPen(QtGui.QColor(TXT))
        f = QtGui.QFont("Segoe UI", 38, QtGui.QFont.Weight.Bold)
        p.setFont(f)
        big = f"{self.pct}%" if self.pct is not None else "—"
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, big)
        p.setPen(QtGui.QColor(SUB))
        p.setFont(QtGui.QFont("Segoe UI", 9))
        sub = ("CHARGING" if self.charging else "BATTERY") if self.pct is not None else "NO TELEMETRY"
        p.drawText(QRectF(14, 110, 156, 30), Qt.AlignmentFlag.AlignHCenter, sub)


class Card(QtWidgets.QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        self.v = QtWidgets.QVBoxLayout(self)
        self.v.setContentsMargins(18, 16, 18, 16)
        self.v.setSpacing(12)
        lab = QtWidgets.QLabel(title)
        lab.setObjectName("ctitle")
        lab.setSizePolicy(QtWidgets.QSizePolicy.Policy.Maximum,
                          QtWidgets.QSizePolicy.Policy.Fixed)
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(lab)
        trow.addStretch()
        self.v.addLayout(trow)


# ============================================================ main window
class Panel(QtWidgets.QMainWindow):
    def __init__(self, dev):
        super().__init__()
        self.dev = dev
        self.setWindowTitle("Razer Barracuda 2.4")
        self.setFixedWidth(420)
        self._build()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(4000)
        QtCore.QTimer.singleShot(150, self.refresh)

    def _build(self):
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        root = QtWidgets.QVBoxLayout(c)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- hero header
        hero = QtWidgets.QFrame()
        hero.setObjectName("hero")
        hero.setFixedHeight(96)
        hl = QtWidgets.QHBoxLayout(hero)
        hl.setContentsMargins(22, 0, 22, 0)
        accent = QtWidgets.QFrame()
        accent.setObjectName("accent")
        accent.setFixedSize(4, 46)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(3)
        brand = QtWidgets.QLabel("RAZER")
        brand.setObjectName("hbrand")
        bf = brand.font()
        bf.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 4)
        brand.setFont(bf)
        name = QtWidgets.QLabel("BARRACUDA 2.4")
        name.setObjectName("hname")
        col.addWidget(brand)
        col.addWidget(name)
        hl.addWidget(accent)
        hl.addSpacing(12)
        hl.addLayout(col)
        hl.addStretch()
        self.pill = QtWidgets.QLabel("● …")
        self.pill.setObjectName("pill")
        hl.addWidget(self.pill, alignment=Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(hero)
        line = QtWidgets.QFrame()
        line.setObjectName("heroline")
        line.setFixedHeight(2)
        root.addWidget(line)

        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(14)
        root.addLayout(body)

        # ---- battery ring
        self.ring = BatteryRing()
        body.addWidget(self.ring, alignment=Qt.AlignmentFlag.AlignHCenter)

        # ---- sidetone
        sc = Card("SIDETONE · hear your own mic")
        rowt = QtWidgets.QHBoxLayout()
        rowt.addWidget(QtWidgets.QLabel("Enabled"))
        rowt.addStretch()
        self.st_on = Toggle()
        self.st_on.toggled.connect(self.apply_sidetone)
        rowt.addWidget(self.st_on)
        sc.v.addLayout(rowt)
        rowl = QtWidgets.QHBoxLayout()
        self.st_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.st_slider.setRange(0, 16)
        self.st_slider.setValue(8)
        self.st_badge = QtWidgets.QLabel("8")
        self.st_badge.setObjectName("badge")
        self.st_badge.setFixedWidth(34)
        self.st_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.st_slider.valueChanged.connect(lambda v: self.st_badge.setText(str(v)))
        self.st_slider.sliderReleased.connect(self.apply_sidetone)
        rowl.addWidget(QtWidgets.QLabel("Level"))
        rowl.addWidget(self.st_slider)
        rowl.addWidget(self.st_badge)
        sc.v.addLayout(rowl)
        body.addWidget(sc)

        # ---- power saving (segmented)
        pc = Card("POWER SAVING · auto-off on battery")
        seg = QtWidgets.QHBoxLayout()
        seg.setSpacing(0)
        self.ps_group = QtWidgets.QButtonGroup(self)
        for i, (lbl, mins) in enumerate([("Off", 0), ("15m", 15), ("30m", 30),
                                         ("45m", 45), ("60m", 60)]):
            b = QtWidgets.QPushButton(lbl)
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setProperty("mins", mins)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == 0:
                b.setChecked(True)
            b.clicked.connect(self.apply_power)
            self.ps_group.addButton(b)
            seg.addWidget(b)
        pc.v.addLayout(seg)
        body.addWidget(pc)

        # ---- equalizer (PC-side DSP via EasyEffects; headset EQ is BLE-only)
        ec = Card("EQUALIZER · software (EasyEffects)")
        note = QtWidgets.QLabel(
            "The headset's built-in EQ is Bluetooth-only — over the 2.4 dongle a "
            "preset write is ignored (Synapse applies EQ as PC-side DSP). Use the "
            "software EQ below for a 10-band preset on the Barracuda output.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        ec.v.addWidget(note)
        eqb = QtWidgets.QPushButton("Open software EQ…  (EasyEffects)")
        eqb.setObjectName("ghost")
        eqb.setCursor(Qt.CursorShape.PointingHandCursor)
        eqb.clicked.connect(self.open_eq)
        ec.v.addWidget(eqb)
        body.addWidget(ec)

        body.addStretch()
        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("hint")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.status)

        self.setStyleSheet(STYLE)
        self.adjustSize()

    # ---- actions
    def apply_sidetone(self, *_):
        if self.st_on.isChecked():
            self.dev.set(0x98, 1)
            time.sleep(0.02)
            self.dev.set(0x99, self.st_slider.value())
            self._flash(f"Sidetone on · level {self.st_slider.value()}")
        else:
            self.dev.set(0x98, 0)
            self._flash("Sidetone off")

    def apply_power(self):
        b = self.ps_group.checkedButton()
        m = b.property("mins")
        self.dev.set(0xac, m)
        self._flash("Power-saving " + ("off" if m == 0 else f"· {m} min"))

    def open_eq(self):
        if shutil.which("easyeffects"):
            dst = os.path.expanduser("~/.config/easyeffects/output/razer-barracuda-eq.json")
            src = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                               "razer-barracuda-eq.json")
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.exists(dst) and os.path.exists(src):
                    shutil.copy(src, dst)
            except OSError:
                pass
            subprocess.Popen(["easyeffects"])
            self._flash("EasyEffects launched · load 'razer-barracuda-eq'")
        else:
            self._flash("Install EasyEffects:  sudo pacman -S easyeffects")

    def _flash(self, msg):
        self.status.setText(msg)
        QtCore.QTimer.singleShot(4000, lambda: self.status.setText(""))

    # ---- refresh
    def refresh(self):
        if not self.dev.present:
            self.dev.open()
        link = self.dev.get(0x20)
        if link is not None:
            self.pill.setText("● connected")
            self.pill.setStyleSheet(f"color:{GREEN};")
        else:
            self.pill.setText("● off / dongle only")
            self.pill.setStyleSheet("color:#5a6066;")
        self.dev.refresh_telemetry()        # arm fresh battery/charging over RF
        time.sleep(0.05)
        batt = self.dev.get(0x21)
        chg = self.dev.get(0x2a)
        self.ring.set_value(batt if (batt is not None and 0 <= batt <= 100) else None, bool(chg))


STYLE = f"""
* {{ font-family:'Segoe UI','Inter','Noto Sans',sans-serif; color:{TXT}; }}
QMainWindow,QWidget {{ background:{BG}; }}
#hero {{ background:#0d140f; }}
#heroline {{ background:{GREEN}; }}
#accent {{ background:{GREEN}; border-radius:2px; }}
#hbrand {{ color:{GREEN}; font-size:11px; font-weight:800; }}
#hname {{ font-size:19px; font-weight:800; }}
#pill {{ font-size:12px; font-weight:600; }}
#card {{ background:{CARD}; border:1px solid {CARD_BRD}; border-radius:14px; }}
#ctitle {{ color:{GREEN}; font-size:11px; font-weight:800; }}
QLabel {{ font-size:13px; background:transparent; }}
#hint {{ color:{SUB}; font-size:11px; }}
#badge {{ background:#0e1a0d; color:{GREEN}; border:1px solid #1c3a17;
          border-radius:8px; padding:3px 0; font-weight:700; }}
QPushButton#primary {{ background:{GREEN}; color:#06210a; border:none;
          border-radius:10px; padding:11px; font-weight:700; font-size:13px; }}
QPushButton#primary:hover {{ background:#54e63a; }}
QPushButton#eq {{ background:#0e1113; border:1px solid {CARD_BRD};
          border-radius:9px; padding:11px 0; font-size:13px; font-weight:600; }}
QPushButton#eq:hover {{ border-color:#3a4147; }}
QPushButton#eq:checked {{ background:{GREEN}; color:#06210a; border-color:{GREEN};
          font-weight:700; }}
QPushButton#ghost {{ background:transparent; border:1px solid {CARD_BRD};
          border-radius:9px; padding:9px; color:{SUB}; font-size:12px; }}
QPushButton#ghost:hover {{ border-color:{GREEN}; color:{TXT}; }}
QPushButton#seg {{ background:#0e1113; border:1px solid {CARD_BRD};
          padding:9px 0; font-size:12px; font-weight:600; }}
QPushButton#seg:first-child {{ border-top-left-radius:9px; border-bottom-left-radius:9px; }}
QPushButton#seg:last-child {{ border-top-right-radius:9px; border-bottom-right-radius:9px; }}
QPushButton#seg:checked {{ background:{GREEN}; color:#06210a; border-color:{GREEN}; }}
QPushButton#seg:hover:!checked {{ border-color:#3a4147; }}
QSlider::groove:horizontal {{ height:6px; background:#1c2024; border-radius:3px; }}
QSlider::handle:horizontal {{ width:18px; height:18px; background:#fff;
          border-radius:9px; margin:-6px 0; }}
QSlider::sub-page:horizontal {{ background:{GREEN}; border-radius:3px; }}
"""


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Razer Barracuda 2.4")
    dev = Barracuda()
    if not dev.present:
        QtWidgets.QMessageBox.critical(None, "Razer Barracuda 2.4",
            "Device not found or no access to its hidraw node.\n"
            "Plug in the dongle and ensure the udev rule is installed.")
        return 1
    win = Panel(dev)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
