"""
Raspberry Pi Zero 2W hardware monitor.

Reads CPU, RAM, disk, temperature, throttling status, and load averages.
All hardware-specific calls are wrapped in try/except so the module
degrades gracefully on non-Pi systems or if vcgencmd is unavailable.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import psutil


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class CpuInfo:
    value: float  # percent
    state: str    # low | medium | high

@dataclass
class RamInfo:
    total_mb: float
    used_mb: float
    available_mb: float
    total_gb: float
    percent: float

@dataclass
class DiskInfo:
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float

@dataclass
class TempInfo:
    value: float
    state: str  # cool | warm | hot

@dataclass
class ThrottleInfo:
    raw: str
    undervoltage: bool
    throttled: bool
    state: str  # ok | warning

@dataclass
class LoadInfo:
    one: dict  # {"value": float, "state": str}
    five: dict
    fifteen: dict
    cores: int

@dataclass
class TimeInfo:
    datetime: str
    uptime_hours: int

@dataclass
class SystemSnapshot:
    time: TimeInfo = field(default_factory=lambda: TimeInfo("", 0))
    platform: str = ""
    cpu: Optional[CpuInfo] = None
    ram: Optional[RamInfo] = None
    disk: Optional[DiskInfo] = None
    temp: Optional[TempInfo] = None
    throttled: Optional[ThrottleInfo] = None
    load_avg: Optional[LoadInfo] = None


# ---------------------------------------------------------------------------
# Metric collectors
# ---------------------------------------------------------------------------

def cpu() -> CpuInfo:
    val = psutil.cpu_percent(interval=0.2)
    state = "low" if val < 30 else "medium" if val < 70 else "high"
    return CpuInfo(value=val, state=state)


def ram() -> RamInfo:
    m = psutil.virtual_memory()
    return RamInfo(
        total_mb=round(m.total / 1024 / 1024, 1),
        used_mb=round(m.used / 1024 / 1024, 1),
        available_mb=round(m.available / 1024 / 1024, 1),
        total_gb=round(m.total / 1024 / 1024 / 1024, 2),
        percent=m.percent,
    )


def disk() -> DiskInfo:
    d = psutil.disk_usage("/")
    return DiskInfo(
        total_gb=round(d.total / 1024**3, 2),
        used_gb=round(d.used / 1024**3, 2),
        free_gb=round(d.free / 1024**3, 2),
        percent=d.percent,
    )


def temp() -> Optional[TempInfo]:
    """Read CPU temperature from the Raspberry Pi thermal zone."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            t = int(f.read()) / 1000.0
        state = "cool" if t < 50 else "warm" if t < 70 else "hot"
        return TempInfo(value=round(t, 1), state=state)
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        return None


def throttled() -> Optional[ThrottleInfo]:
    """Check for undervoltage / throttling via vcgencmd."""
    try:
        raw = subprocess.check_output(["vcgencmd", "get_throttled"], timeout=5).decode()
        val = int(raw.split("=")[1], 16)
        return ThrottleInfo(
            raw=raw.strip(),
            undervoltage=bool(val & 1),
            throttled=bool(val & (1 << 0)),
            state="ok" if val == 0 else "warning",
        )
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError, OSError):
        return None


def load_avg() -> LoadInfo:
    l1, l5, l15 = os.getloadavg()
    cores = os.cpu_count() or 1

    def _label(x: float) -> str:
        ratio = x / cores
        return "low" if ratio < 0.3 else "medium" if ratio < 0.7 else "high"

    return LoadInfo(
        one={"value": round(l1, 2), "state": _label(l1)},
        five={"value": round(l5, 2), "state": _label(l5)},
        fifteen={"value": round(l15, 2), "state": _label(l15)},
        cores=cores,
    )


def get_time() -> TimeInfo:
    boot = psutil.boot_time()
    uptime = time.time() - boot
    return TimeInfo(
        datetime=datetime.now().strftime("%d-%m-%Y %H:%M"),
        uptime_hours=int(uptime),
    )


# ---------------------------------------------------------------------------
# Snapshot & rendering
# ---------------------------------------------------------------------------

def snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        time=get_time(),
        platform=platform.platform(),
        cpu=cpu(),
        ram=ram(),
        disk=disk(),
        temp=temp(),
        throttled=throttled(),
        load_avg=load_avg(),
    )


def _bar(value: float, width: int = 5) -> str:
    value = max(0.0, min(value, 100.0))
    filled = int((value / 100) * width)
    return "█" * filled + "░" * (width - filled)


def render(data: SystemSnapshot) -> str:
    lines = ["=" * 60]
    lines.append(f"SYS MONITOR - {data.time.datetime}")
    uptime = data.time.uptime_hours
    lines.append(f"UPTIME       : {uptime // 3600}h {(uptime % 3600) // 60}m")
    lines.append("=" * 60)

    if data.cpu:
        c = data.cpu
        lines.append(
            f"CPU          : {c.value:5.1f}% [{c.state:<6}] {_bar(c.value)}"
        )

    if data.ram:
        r = data.ram
        lines.append(
            f"RAM          : {r.percent:5.1f}% {_bar(r.percent)} "
            f"({r.used_mb:.1f}MB/{r.total_mb:.1f}MB)"
        )

    if data.disk:
        d = data.disk
        lines.append(
            f"DISK         : {d.percent:5.1f}% {_bar(d.percent)} "
            f"({d.used_gb:.2f}GB/{d.total_gb:.2f}GB)"
        )

    if data.temp:
        t = data.temp
        lines.append(f"TEMP         : {t.value:5.1f}°C [{t.state}]")

    if data.load_avg:
        ld = data.load_avg
        lines.append(f"LOAD (1m)    : {ld.one['value']:.2f} [{ld.one['state']}]")

    if data.throttled and data.throttled.state == "warning":
        lines.append(f"THROTTLED    : WARNING ({data.throttled.raw})")

    lines.append("=" * 60)
    return "\n".join(lines)


def get_report() -> str:
    return render(snapshot())


if __name__ == "__main__":
    print(get_report())
