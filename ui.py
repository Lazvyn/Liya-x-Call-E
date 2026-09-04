import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

try:
    from memory.memory_manager import (
        append_history_entry, load_history, load_history_grouped_by_date,
    )
except Exception:
    # UI must still run standalone even if the memory package isn't importable
    def append_history_entry(line: str) -> None: ...
    def load_history(limit: int = 60) -> list: return []
    def load_history_grouped_by_date() -> dict: return {}

try:
    from agent.task_queue import get_queue
except Exception:
    def get_queue(): return None

from PyQt6.QtCore import (
    QEasingCurve, QMimeData, QObject, QPointF, QPropertyAnimation, QRectF,
    QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QFontDatabase,
    QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar, QGraphicsDropShadowEffect,
)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"

_DEFAULT_W, _DEFAULT_H = 1180, 760
_MIN_W,     _MIN_H     = 900, 580
_LEFT_W  = 160
_RIGHT_W = 760

_OS = platform.system()   # "Windows" | "Darwin" | "Linux"


# ---------------------------------------------------------------------------
# Colour palette — cyan/teal cyberpunk theme
# ---------------------------------------------------------------------------
class C:
    BG        = "#0b0209"
    PANEL     = "#1c0813"
    PANEL2    = "#241019"
    BORDER    = "#7a2350"
    BORDER_B  = "#ff5fa8"
    BORDER_A  = "#c9457f"
    PRI       = "#ff4fa3"
    PRI_DIM   = "#cc3d82"
    PRI_GHO   = "#3d0f26"
    ACC       = "#ff9ecf"
    ACC2      = "#ffd1e8"
    GREEN     = "#4dffb8"
    GREEN_D   = "#1acc8f"
    RED       = "#ff3366"
    MUTED_C   = "#a94a78"
    TEXT      = "#ffe3f1"
    TEXT_DIM  = "#7a4a63"
    TEXT_MED  = "#e08fc0"
    WHITE     = "#fff2f9"
    DARK      = "#12040b"
    BAR_BG    = "#241019"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


def rgba(h: str, a: int) -> str:
    """Hex colour + 0-255 alpha -> Qt-stylesheet rgba(...) string (glass fills)."""
    h = h.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"


def glass_panel(border_col: str = None, radius: int = 14, border_a: int = 140) -> str:
    """Reusable frosted-glass QSS fill: translucent gradient + soft glowing border."""
    border_col = border_col or C.BORDER_B
    return (
        f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {rgba(C.PANEL2, 165)}, stop:1 {rgba(C.BG, 195)}); "
        f"border: 1px solid {rgba(border_col, border_a)}; "
        f"border-radius: {radius}px;"
    )


def glass_glow(widget, color: str = None, radius: int = 28, alpha: int = 130):
    """Attach a soft outer glow (drop shadow) so a panel reads as lit glass."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(radius)
    eff.setOffset(0, 0)
    eff.setColor(qcol(color or C.PRI, alpha))
    widget.setGraphicsEffect(eff)
    return eff


# ---------------------------------------------------------------------------
# Background system-metrics collector (runs in a daemon thread)
# ---------------------------------------------------------------------------
class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0
        self.gpu  = -1.0
        self.tmp  = -1.0
        self._lock       = threading.Lock()
        self._last_net   = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running    = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()
        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2,
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1,
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2,
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2,
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature "
                     "-Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3,
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()


# ---------------------------------------------------------------------------
# Speedometer widget — replaces flat MetricBar
# ---------------------------------------------------------------------------
class Speedometer(QWidget):
    """A semicircular speedometer gauge for displaying a metric."""

    def __init__(self, label: str, unit: str = "%", color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._unit  = unit
        self._color = color
        self._value = 0.0
        self._text  = "--"
        self._anim_val = 0.0
        self._target   = 0.0
        self.setFixedSize(148, 90)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._animate)
        self._tmr.start(30)

    def set_value(self, pct: float, text: str):
        self._target = max(0.0, min(100.0, pct))
        self._text   = text

    def _animate(self):
        diff = self._target - self._anim_val
        if abs(diff) > 0.3:
            self._anim_val += diff * 0.12
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        # background
        p.fillRect(self.rect(), qcol(C.BG))

        cx = W / 2
        cy = H - 16
        r  = min(W, H * 2) * 0.42
        r_inner = r * 0.60

        # track arc (background) — 180 degrees, from 180° to 0° (bottom half of circle)
        start_angle = 180 * 16
        span_angle  = -180 * 16

        pen_bg = QPen(qcol(C.BORDER, 180), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen_bg)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start_angle, span_angle)

        # colored fill arc
        fill_span = int(-180 * 16 * self._anim_val / 100)
        if fill_span != 0:
            col = self._color
            if self._anim_val > 85:
                col = C.RED
            elif self._anim_val > 65:
                col = "#ffb347"

            # glow effect — draw multiple layers
            for gw, ga in [(14, 30), (10, 60), (7, 120), (5, 200)]:
                gpen = QPen(qcol(col, ga), gw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
                p.setPen(gpen)
                p.drawArc(QRectF(cx - r, cy - r, r * 2, r * 2), start_angle, fill_span)

        # tick marks
        for deg in range(0, 181, 18):
            rad = math.radians(180 - deg)
            is_major = deg % 36 == 0
            t_out = r + 3
            t_in  = r - (7 if is_major else 4)
            tick_col = qcol(C.BORDER_B, 200 if is_major else 100)
            p.setPen(QPen(tick_col, 1.5 if is_major else 1))
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + t_in  * math.cos(rad), cy - t_in  * math.sin(rad)),
            )

        # needle
        needle_angle = math.radians(180 - self._anim_val * 1.8)
        n_len = r * 0.75
        n_tip = QPointF(cx + n_len * math.cos(needle_angle), cy - n_len * math.sin(needle_angle))
        n_back = QPointF(cx - (r * 0.15) * math.cos(needle_angle),
                         cy + (r * 0.15) * math.sin(needle_angle))

        needle_col = self._color if self._anim_val <= 65 else (
            "#ffb347" if self._anim_val <= 85 else C.RED
        )
        p.setPen(QPen(qcol(needle_col, 40), 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(n_back, n_tip)
        p.setPen(QPen(qcol(needle_col, 220), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(n_back, n_tip)

        # center hub
        hub_r = 5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(self._color, 200)))
        p.drawEllipse(QPointF(cx, cy), hub_r, hub_r)
        p.setBrush(QBrush(qcol(C.BG)))
        p.drawEllipse(QPointF(cx, cy), hub_r - 2, hub_r - 2)

        # value text
        p.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(self._color if self._text != "--" else C.TEXT_DIM), 1))
        p.drawText(QRectF(0, cy - 26, W, 18),
                   Qt.AlignmentFlag.AlignCenter, self._text)

        # label text
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, cy + 4, W, 13),
                   Qt.AlignmentFlag.AlignCenter, self._label)


# ---------------------------------------------------------------------------
# HUD canvas — animated orb / face  (pink palette)
# ---------------------------------------------------------------------------
class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._face_px: QPixmap | None = None
        self._load_face(face_path)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._tgt_scale = random.uniform(1.06, 1.14)
                self._tgt_halo  = random.uniform(145, 190)
            elif self.muted:
                self._tgt_scale = random.uniform(0.998, 1.002)
                self._tgt_halo  = random.uniform(15, 28)
            else:
                self._tgt_scale = random.uniform(1.001, 1.008)
                self._tgt_halo  = random.uniform(48, 68)
            self._last_t = now

        sp = 0.38 if self.speaking else 0.15
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        speeds = [1.3, -0.9, 2.0] if self.speaking else [0.55, -0.35, 0.9]
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else 1.3)) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else -0.75)) % 360

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else 2.0
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        if len(self._pulses) < 3 and random.random() < (0.07 if self.speaking else 0.025):
            self._pulses.append(0.0)

        if self.speaking and random.random() < 0.28:
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s,
                cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4,
                1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))

        W, H  = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw    = min(W, H)

        # subtle pink grid dots
        p.setPen(QPen(qcol("#2a0a1c"), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                p.drawPoint(x, y)

        r_face = fw * 0.31

        # halo glow — pink tones
        halo_col = C.MUTED_C if self.muted else C.PRI
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(halo_col, a)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(halo_col, a)
            p.setPen(QPen(col, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.MUTED_C if self.muted else C.PRI, a_val)
            p.setPen(QPen(col, w_r))
            p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr    = fw * 0.50
        sa    = min(255, int(self._halo * 1.5))
        ex    = 75 if self.speaking else 44
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.setPen(QPen(qcol(C.MUTED_C if self.muted else C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets — pink
        bl       = 24
        bc       = qcol(C.PRI, 210)
        hl, hr   = cx - fw // 2, cx + fw // 2
        ht, hb   = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl, ht, 1, 1), (hr, ht, -1, 1),
                                (hl, hb, 1, -1), (hr, hb, -1, -1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # face image or fallback orb
        if self._face_px:
            fsz    = int(fw * 0.62 * self._scale)
            scaled = self._face_px.scaled(
                fsz, fsz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(int(cx - fsz / 2), int(cy - fsz / 2), scaled)
        else:
            orb_r = int(fw * 0.27 * self._scale)
            oc    = (150, 20, 80) if self.muted else (255, 60, 140)
            for i in range(8, 0, -1):
                r2  = int(orb_r * i / 8)
                frc = i / 8
                a   = max(0, min(255, int(self._halo * 1.1 * frc)))
                p.setBrush(QBrush(QColor(
                    int(oc[0] * frc), int(oc[1] * frc), int(oc[2] * frc + 40 * frc), a,
                )))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - r2, cy - r2, r2 * 2, r2 * 2))
            p.setPen(QPen(qcol(C.PRI, min(255, int(self._halo * 2))), 1))
            p.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
            p.drawText(QRectF(cx - 80, cy - 14, 160, 28),
                       Qt.AlignmentFlag.AlignCenter, "LIYA")

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ACC, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status label
        sy = cy + fw * 0.40
        if self.muted:
            txt, col = "⊘  MUTED",      qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "♪  SPEAKING",   qcol(C.ACC)
        elif self.state == "THINKING":
            sym      = "✦" if self._blink else "✧"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym      = "✿" if self._blink else "❀"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym      = "♥" if self._blink else "♡"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym      = "♥" if self._blink else "♡"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform bars
        wy      = sy + 30
        N, bw   = 36, 8
        wx0     = (W - N * bw) / 2
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            elif self.speaking:
                hgt = random.randint(3, 20)
                cl  = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
            else:
                hgt = int(3 + 2 * math.sin(self._tick * 0.09 + i * 0.6))
                cl  = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)


# ---------------------------------------------------------------------------
# Typewriter log widget
# ---------------------------------------------------------------------------
class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {rgba(C.PANEL, 175)};
                color: {C.TEXT};
                border: 1px solid {rgba(C.BORDER_B, 110)};
                border-radius: 14px;
                padding: 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing = False
        self._text   = ""
        self._pos    = 0
        self._tag    = "sys"
        self._tmr    = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def reset(self):
        """Clear the widget and stop/discard any in-flight typewriter
        animation and queued lines — safe to call mid-typing."""
        self._tmr.stop()
        self._queue.clear()
        self._typing = False
        self.clear()

    def add_history_line(self, text: str, ts: str = ""):
        """Render a past-session line instantly (no typewriter), dimmed,
        so it reads clearly as history rather than a live event."""
        tl = text.lower()
        if   tl.startswith("you:"):  base = C.TEXT_MED
        elif tl.startswith("liya:"): base = C.PRI_DIM
        else:                        base = C.TEXT_MED

        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(qcol(base)))
        prefix = f"[{ts}] " if ts else ""
        cur.insertText(f"{prefix}{text}\n", fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def add_separator(self, label: str):
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(qcol(C.BORDER_B)))
        cur.insertText(f"── {label} ──\n", fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("liya:"):   self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


# ---------------------------------------------------------------------------
# File drop zone
# ---------------------------------------------------------------------------
_FILE_ICONS = {
    "image":   ("🖼", "#ff4fa3"), "video":   ("🎬", "#ff9ecf"),
    "audio":   ("🎵", "#44ccff"), "pdf":     ("📄", "#ff4466"),
    "word":    ("📝", "#88aaff"), "excel":   ("📊", "#4dffb8"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#ffd1e8"),
    "data":    ("🔧", "#66d9ff"), "unknown": ("📎", "#e08fc0"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(
        ["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(
        ["mp4","avi","mov","mkv","wmv","flv","webm","m4v"], "video"),
    **dict.fromkeys(
        ["mp3","wav","ogg","m4a","aac","flac","wma","opus"], "audio"),
    **dict.fromkeys(["pdf"], "pdf"),
    **dict.fromkeys(["doc","docx"], "word"),
    **dict.fromkeys(["xls","xlsx","ods"], "excel"),
    **dict.fromkeys(["ppt","pptx"], "pptx"),
    **dict.fromkeys(
        ["py","js","ts","jsx","tsx","html","css","java","c","cpp",
         "cs","go","rs","rb","php","swift","kt","sh","sql","lua"], "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"], "archive"),
    **dict.fromkeys(["txt","md","rst","log"], "text"),
    **dict.fromkeys(["csv","tsv","json","xml"], "data"),
}


def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")


def _fmt_size(size: int) -> str:
    if   size < 1024:      return f"{size} B"
    elif size < 1024**2:   return f"{size/1024:.1f} KB"
    elif size < 1024**3:   return f"{size/1024**2:.1f} MB"
    else:                  return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self._current_file: str | None = None
        self._hovering    = False
        self._drag_over   = False
        self._dash_offset = 0.0

        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True
            self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True
        self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False
        self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for Liya", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: "FileDropZone"):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p    = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 4
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol(
            "#3d1226" if z._drag_over else ("#241019" if z._hovering else C.PANEL),
            190,
        )
        p.setBrush(QBrush(bg_col))
        p.setPen(QPen(qcol(C.BORDER_B, 90), 1))
        p.drawRoundedRect(rect, 14, 14)

        if z._current_file:  border_col = qcol(C.GREEN, 200)
        elif z._drag_over:   border_col = qcol(C.PRI, 230)
        elif z._hovering:    border_col = qcol(C.BORDER_B, 200)
        else:                border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 14, 14)

        if z._current_file:  self._paint_file(p, W, H)
        elif z._drag_over:   self._paint_drag_over(p, W, H)
        else:                self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # upload arrow
        p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy + 3))
        p.drawLine(QPointF(cx - 6, cy - 4), QPointF(cx, cy - 10))
        p.drawLine(QPointF(cx + 6, cy - 4), QPointF(cx, cy - 10))
        p.drawLine(QPointF(cx - 10, cy + 3), QPointF(cx + 10, cy + 3))
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 6, W, 13), Qt.AlignmentFlag.AlignCenter,
                   "♡  Drop file or Click to Browse  ♡")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 16))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 18, W, 26), Qt.AlignmentFlag.AlignCenter, "♡")
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 10, W, 13), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path     = Path(self._z._current_file)
        cat      = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 6, 44
        p.setFont(QFont("Segoe UI Emoji", 18) if _OS == "Windows" else QFont("Arial", 18))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 4
        tw = W - tx - 32

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 38 else path.name[:35] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 13),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 30, 0, 24, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 30:
            z.clear_file()
        else:
            z.mousePressEvent(e)


# ---------------------------------------------------------------------------
# First-run setup overlay
# ---------------------------------------------------------------------------
class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(13, 0, 16, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 10px;
            }}
        """)

        detected     = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("✦  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure Liya before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #170610; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 4px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout()
        os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows", "⊞  Windows"), ("mac", "  macOS"), ("linux", "🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("♥  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 4px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {
            "windows": (C.PRI,   "#2a0a1c"),
            "mac":     (C.ACC2,  "#22091a"),
            "linux":   (C.GREEN, "#1a0c14"),
        }
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 4px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #170610; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 4px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("LIYA")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command           = None
        self._muted                    = False
        self._current_file: str | None = None
        self._overlay: SetupOverlay | None = None

        # ── central widget ──────────────────────────────────────────────────
        self._central = QWidget()
        self._central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(self._central)

        root = QVBoxLayout(self._central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = self._build_header()
        glass_glow(header, C.PRI, radius=22, alpha=90)
        root.addWidget(header)

        # ── main body: left | center | right ────────────────────────────────
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._sessions_panel = self._build_sessions_panel()
        self._sessions_panel.setMaximumWidth(0)   # collapsed by default
        self._sessions_open  = False
        body.addWidget(self._sessions_panel, stretch=0)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # center column: hud + bottom input/file strip
        center_col = QVBoxLayout()
        center_col.setContentsMargins(0, 0, 0, 0)
        center_col.setSpacing(0)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center_col.addWidget(self.hud, stretch=1)

        center_col.addWidget(self._build_center_bottom())

        body.addLayout(center_col, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # ── timers ──────────────────────────────────────────────────────────
        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        # ── cross-thread signals ─────────────────────────────────────────────
        self._log_sig.connect(self._handle_log)
        self._state_sig.connect(self._apply_state)

        # ── replay previous session's conversation into the Activity Log ────
        self._load_conversation_history()

        # ── config / setup ───────────────────────────────────────────────────
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        # ── shortcuts ────────────────────────────────────────────────────────
        QShortcut(QKeySequence("F4"),  self).activated.connect(self._toggle_mute)
        QShortcut(QKeySequence("F11"), self).activated.connect(self._toggle_fullscreen)

    # ── resize: reposition overlay if visible ───────────────────────────────
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            self._reposition_overlay()

    def _reposition_overlay(self):
        ow, oh = 460, 390
        cw = self._central
        self._overlay.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )

    # ── fullscreen ───────────────────────────────────────────────────────────
    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── metrics update ───────────────────────────────────────────────────────
    def _update_metrics(self):
        snap = _metrics.snapshot()

        cpu = snap["cpu"]
        self._spdm_cpu.set_value(cpu, f"{cpu:.0f}%")

        mem = snap["mem"]
        self._spdm_mem.set_value(mem, f"{mem:.0f}%")

        net = snap["net"]
        net_str = f"{net*1024:.0f}K/s" if net < 1.0 else f"{net:.1f}M/s"
        self._spdm_net.set_value(min(100, net * 10), net_str)

        gpu = snap["gpu"]
        if gpu >= 0:
            self._spdm_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._spdm_gpu.set_value(0, "N/A")

        tmp = snap["tmp"]
        if tmp >= 0:
            self._spdm_tmp.set_value(min(100, tmp), f"{tmp:.0f}°C")
        else:
            self._spdm_tmp.set_value(0, "N/A")

        try:
            elapsed = time.time() - psutil.boot_time()
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            self._proc_lbl.setText(f"PROC  {len(psutil.pids())}")
        except Exception:
            self._proc_lbl.setText("PROC  --")

        self._update_tasks_panel()

    # ── task queue panel ─────────────────────────────────────────────────────
    _TASK_COLORS = {
        "pending":   C.ACC2,
        "running":   C.PRI,
        "completed": C.GREEN,
        "failed":    C.RED,
        "cancelled": C.TEXT_DIM,
    }
    _TASK_ICONS = {
        "pending":   "⏳",
        "running":   "▶",
        "completed": "✔",
        "failed":    "✖",
        "cancelled": "⊘",
    }

    def _update_tasks_panel(self):
        if not hasattr(self, "_tasks_list_lay"):
            return
        queue = get_queue()
        tasks = queue.get_all_statuses() if queue else []

        # queued/running first (oldest first), then finished (most recent first)
        active   = [t for t in tasks if t["status"] in ("pending", "running")]
        finished = [t for t in tasks if t["status"] not in ("pending", "running")]
        active.sort(key=lambda t: t.get("created_at") or "")
        finished.sort(key=lambda t: t.get("finished_at") or "", reverse=True)
        ordered = active + finished[:8]

        while self._tasks_list_lay.count() > 1:
            item = self._tasks_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not ordered:
            empty = QLabel("No tasks queued.")
            empty.setFont(QFont("Courier New", 7))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._tasks_list_lay.insertWidget(0, empty)
            return

        for i, t in enumerate(ordered):
            status = t.get("status", "pending")
            color  = self._TASK_COLORS.get(status, C.TEXT_DIM)
            icon   = self._TASK_ICONS.get(status, "•")
            row = QLabel(f"{icon}  [{status.upper()}]  {t.get('goal', '')}")
            row.setFont(QFont("Courier New", 7))
            row.setWordWrap(True)
            row.setStyleSheet(
                f"color: {color}; background: transparent; "
                f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 3px;"
            )
            self._tasks_list_lay.insertWidget(i, row)

    # ── header ───────────────────────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(54)
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {rgba(C.PANEL2, 210)}, stop:1 {rgba(C.DARK, 235)}); "
            f"border-bottom: 1px solid {rgba(C.BORDER_B, 160)};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(16, 0, 16, 0)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        self._sessions_toggle = QPushButton("☰")
        self._sessions_toggle.setFixedSize(26, 26)
        self._sessions_toggle.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self._sessions_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sessions_toggle.setToolTip("Toggle past sessions")
        self._sessions_toggle.setStyleSheet(f"""
            QPushButton {{
                background: {rgba(C.PANEL, 200)}; color: {C.PRI};
                border: 1px solid {rgba(C.PRI_DIM, 200)}; border-radius: 10px;
            }}
            QPushButton:hover {{ background: {rgba(C.PRI_GHO, 220)}; border: 1px solid {C.PRI}; }}
        """)
        self._sessions_toggle.clicked.connect(self._toggle_sessions)
        lay.addWidget(self._sessions_toggle)
        lay.addSpacing(10)

        lay.addWidget(_badge("LIYA", C.PRI_DIM))
        lay.addStretch()

        mid = QVBoxLayout()
        mid.setSpacing(1)
        title = QLabel("✦ LIYA ✦")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        mid.addWidget(title)
        sub = QLabel("My Artificial Linked Intelligence Interface Terminal")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(QFont("Courier New", 7))
        sub.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(sub)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout()
        right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    # ── left panel — speedometers ─────────────────────────────────────────────
    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {rgba(C.DARK, 235)}, stop:1 {rgba(C.PANEL, 200)}); "
            f"border-right: 1px solid {rgba(C.BORDER, 200)};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 10, 6, 10)
        lay.setSpacing(4)

        hdr = QLabel("✦ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(
            f"color: {C.PRI}; background: transparent; "
            f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;"
        )
        hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(hdr)
        lay.addSpacing(2)

        # Speedometer gauges
        self._spdm_cpu = Speedometer("CPU",  "%",    C.PRI)
        self._spdm_mem = Speedometer("MEM",  "%",    "#ff8fc9")
        self._spdm_net = Speedometer("NET",  "MB/s", "#ff5fa8")
        self._spdm_gpu = Speedometer("GPU",  "%",    "#ffb3d9")
        self._spdm_tmp = Speedometer("TEMP", "°C",   "#ff6f91")

        for spdm in [self._spdm_cpu, self._spdm_mem, self._spdm_net,
                     self._spdm_gpu, self._spdm_tmp]:
            lay.addWidget(spdm, alignment=Qt.AlignmentFlag.AlignHCenter)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        info_panel.setStyleSheet(glass_panel(C.BORDER_B, radius=10, border_a=120))
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(6, 5, 6, 5)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(
            f"color: {C.GREEN}; background: transparent; border: none;"
        )
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent; border: none;"
        )
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl  = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("LIYA\nCORE",      C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                f"color: {col}; background: {rgba(C.PANEL2, 170)};"
                f"border: 1px solid {rgba(C.BORDER_A, 160)}; border-radius: 10px; padding: 4px;"
            )
            lay.addWidget(lbl)

        return w

    # ── sessions drawer — collapsible past-conversation list ───────────────────
    _SESSIONS_W = 220

    def _build_sessions_panel(self) -> QWidget:
        w = QWidget()
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {rgba(C.DARK, 235)}, stop:1 {rgba(C.PANEL, 200)}); "
            f"border-right: 1px solid {rgba(C.BORDER, 200)};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 10, 8, 10)
        lay.setSpacing(6)

        hdr = QLabel("✦ SESSIONS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(
            f"color: {C.PRI}; background: transparent; "
            f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;"
        )
        lay.addWidget(hdr)

        self._live_btn = QPushButton("🔴  LIVE  (current)")
        self._live_btn.setFixedHeight(28)
        self._live_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._live_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._live_btn.setStyleSheet(f"""
            QPushButton {{
                background: {rgba(C.PRI_GHO, 210)}; color: {C.PRI};
                border: 1px solid {C.PRI}; border-radius: 10px; text-align: left; padding-left: 8px;
            }}
            QPushButton:hover {{ background: {rgba(C.PANEL2, 230)}; }}
        """)
        self._live_btn.clicked.connect(self._return_to_live)
        lay.addWidget(self._live_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 20px; }}
        """)
        self._sessions_list = QWidget()
        self._sessions_list.setStyleSheet("background: transparent;")
        self._sessions_list_lay = QVBoxLayout(self._sessions_list)
        self._sessions_list_lay.setContentsMargins(0, 0, 0, 0)
        self._sessions_list_lay.setSpacing(4)
        self._sessions_list_lay.addStretch()
        scroll.setWidget(self._sessions_list)
        lay.addWidget(scroll, stretch=1)

        self._refresh_sessions_list()
        return w

    def _refresh_sessions_list(self):
        # clear existing rows (keep the trailing stretch)
        while self._sessions_list_lay.count() > 1:
            item = self._sessions_list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        grouped = load_history_grouped_by_date()
        if not grouped:
            empty = QLabel("No past sessions yet.")
            empty.setFont(QFont("Courier New", 7))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            empty.setWordWrap(True)
            self._sessions_list_lay.insertWidget(0, empty)
            return

        for i, (date, entries) in enumerate(grouped.items()):
            btn = QPushButton(f"{date}\n{len(entries)} messages")
            btn.setFixedHeight(40)
            btn.setFont(QFont("Courier New", 7))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {rgba(C.PANEL, 190)}; color: {C.TEXT_MED};
                    border: 1px solid {rgba(C.BORDER_B, 100)}; border-radius: 10px;
                    text-align: left; padding: 4px 8px;
                }}
                QPushButton:hover {{ border: 1px solid {C.PRI_DIM}; color: {C.WHITE}; }}
            """)
            btn.clicked.connect(lambda _, d=date, e=entries: self._show_session(d, e))
            self._sessions_list_lay.insertWidget(i, btn)

    def _toggle_sessions(self):
        target = 0 if self._sessions_open else self._SESSIONS_W
        self._sessions_open = not self._sessions_open
        if self._sessions_open:
            self._refresh_sessions_list()
        anim = QPropertyAnimation(self._sessions_panel, b"maximumWidth", self)
        anim.setDuration(180)
        anim.setStartValue(self._sessions_panel.maximumWidth())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._sessions_anim = anim  # keep a reference alive

    def _show_session(self, date: str, entries: list):
        self._log.reset()
        self._log.add_separator(f"Session: {date}")
        for e in entries:
            text = e.get("text", "") if isinstance(e, dict) else str(e)
            ts   = e.get("ts", "").split(" ")[-1] if isinstance(e, dict) else ""
            if text:
                self._log.add_history_line(text, ts)
        self._log.add_separator("End of session — press LIVE to return")
        self._live_btn.setText("🔴  LIVE  (view current)")

    def _return_to_live(self):
        self._log.reset()
        self._load_conversation_history()
        self._live_btn.setText("🔴  LIVE  (current)")

    # ── center bottom: file upload + command input ────────────────────────────
    def _build_center_bottom(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(130)
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {rgba(C.DARK, 235)}, stop:1 {rgba(C.PANEL, 210)}); "
            f"border-top: 1px solid {rgba(C.BORDER_B, 150)};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(6)

        # File drop zone (compact, full-width)
        file_row = QHBoxLayout()
        file_row.setSpacing(6)

        file_sec = QLabel("♡  FILE")
        file_sec.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        file_sec.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        file_row.addWidget(file_sec)

        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        file_row.addWidget(self._drop_zone, stretch=1)

        lay.addLayout(file_row)

        # Command input row
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(6)

        cmd_sec = QLabel("♡  CMD")
        cmd_sec.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        cmd_sec.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        cmd_row.addWidget(cmd_sec)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(32)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {rgba("#170610", 210)}; color: {C.WHITE};
                border: 1px solid {rgba(C.BORDER_B, 110)}; border-radius: 12px; padding: 3px 10px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        cmd_row.addWidget(self._input, stretch=1)

        send = QPushButton("♥")
        send.setFixedSize(32, 32)
        send.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {rgba(C.PANEL, 210)}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 16px;
            }}
            QPushButton:hover {{ background: {rgba(C.PRI_GHO, 230)}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        cmd_row.addWidget(send)

        self._mute_btn = QPushButton("🎙")
        self._mute_btn.setFixedSize(32, 32)
        self._mute_btn.setFont(QFont("Courier New", 11))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        cmd_row.addWidget(self._mute_btn)

        lay.addLayout(cmd_row)
        return w

    # ── right panel ──────────────────────────────────────────────────────────
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: qlineargradient(x1:1, y1:0, x2:0, y2:1, "
            f"stop:0 {rgba(C.DARK, 235)}, stop:1 {rgba(C.PANEL, 200)}); "
            f"border-left: 1px solid {rgba(C.BORDER, 200)};"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"✦ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        # ── activity log (left, wide) + task queue (right, narrow) side-by-side ──
        panes = QHBoxLayout()
        panes.setSpacing(8)

        log_col = QVBoxLayout()
        log_col.setSpacing(6)
        log_col.addWidget(_sec("ACTIVITY LOG  ·  CONVERSATION HISTORY"))
        self._log = LogWidget()
        log_col.addWidget(self._log, stretch=1)
        panes.addLayout(log_col, stretch=3)

        task_col = QVBoxLayout()
        task_col.setSpacing(6)
        task_col.addWidget(_sec("TASK QUEUE  ·  QUEUED / FINISHED"))
        task_scroll = QScrollArea()
        task_scroll.setWidgetResizable(True)
        task_scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: 1px solid {rgba(C.BORDER_B, 110)}; border-radius: 12px; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 20px; }}
        """)
        self._tasks_list = QWidget()
        self._tasks_list.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tasks_list.setStyleSheet(
            f"background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {rgba(C.PANEL2, 160)}, stop:1 {rgba(C.PANEL, 190)});"
        )
        self._tasks_list_lay = QVBoxLayout(self._tasks_list)
        self._tasks_list_lay.setContentsMargins(6, 6, 6, 6)
        self._tasks_list_lay.setSpacing(4)
        self._tasks_list_lay.addStretch()
        task_scroll.setWidget(self._tasks_list)
        task_col.addWidget(task_scroll, stretch=1)
        panes.addLayout(task_col, stretch=1)

        lay.addLayout(panes, stretch=1)

        self._update_tasks_panel()

        return w

    # ── footer ───────────────────────────────────────────────────────────────
    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        w.setStyleSheet(
            f"background: {rgba(C.DARK, 235)}; border-top: 1px solid {rgba(C.BORDER_B, 130)};"
        )
        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 0, 14, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl("♥  LIYA  ♥"))
        lay.addStretch()
        lay.addWidget(_fl("© LIYA", C.PRI_DIM))
        return w

    # ── file selected ─────────────────────────────────────────────────────────
    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    # ── mute ──────────────────────────────────────────────────────────────────
    def _toggle_mute(self):
        self._muted    = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {rgba("#2a0f1c", 220)}; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 16px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {rgba("#220a15", 220)}; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 16px;
                }}
                QPushButton:hover {{ background: {rgba("#3d1226", 230)}; }}
            """)

    # ── send command ──────────────────────────────────────────────────────────
    def _send(self):
        txt = self._input.text().strip()
        if not txt:
            return
        self._input.clear()
        self._handle_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    # ── logging (live) ───────────────────────────────────────────────────────
    def _handle_log(self, text: str):
        """Single entry point for all live log lines: renders it, and — for
        actual conversation turns — persists it so it can be replayed as
        history the next time LIYA starts up."""
        self._log.append_log(text)
        tl = text.lower()
        if tl.startswith("you:") or tl.startswith("liya:"):
            threading.Thread(
                target=append_history_entry, args=(text,), daemon=True
            ).start()

    # ── logging (history replay) ─────────────────────────────────────────────
    def _load_conversation_history(self):
        # Previous sessions are no longer auto-replayed into the live log —
        # they stay out of the way and are reachable via the ☰ SESSIONS
        # panel instead (click a past date to view it, LIVE to come back).
        self._log.add_separator("New session")

    # ── state ─────────────────────────────────────────────────────────────────
    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    # ── config ────────────────────────────────────────────────────────────────
    def _check_config(self) -> bool:
        if not API_FILE.exists():
            return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self._central)
        ov.done.connect(self._on_setup_done)
        self._overlay = ov
        self._reposition_overlay()
        ov.show()

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. LIYA online.")


# ---------------------------------------------------------------------------
# Thin shim so callers can do  ui.root.mainloop()
# ---------------------------------------------------------------------------
class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app

    def mainloop(self):
        self._app.exec()

    def protocol(self, *_):
        pass


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------
class LiyaUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    # ── mute property ─────────────────────────────────────────────────────────
    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    # ── current file ──────────────────────────────────────────────────────────
    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    # ── text command callback ─────────────────────────────────────────────────
    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    # ── state / log ───────────────────────────────────────────────────────────
    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    # ── boot gate ─────────────────────────────────────────────────────────────
    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    # ── speaking helpers ──────────────────────────────────────────────────────
    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")