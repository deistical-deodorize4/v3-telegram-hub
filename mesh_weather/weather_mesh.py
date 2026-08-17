"""
Mesh weather — live telemetry from a Meshtastic LoRa solar node.

Connects on-demand to the meshtastic-ble-bridge TCP endpoint (port 4403),
sends a telemetry request for environment metrics (temperature, relative
humidity, barometric pressure) plus device metrics (battery, uptime),
captures the replies, and disconnects immediately.

Design notes
------------
* The bridge proxies a *single* BLE radio, so requests are single-flight
  (a lock serialises them) and the TCP connection is short-lived — connect,
  exchange telemetry, close.  This keeps the bridge free for MeshMonitor
  and avoids holding the radio stream open.
* The firmware only replies to telemetry requests that set ``want_response``.
  The python library's ``sendTelemetry(wantResponse=True)`` installs a
  response handler that calls ``our_exit()`` on a no-response NAK (which
  would kill the bot), so we build the request with ``sendData`` and a safe
  custom ``onResponse`` instead.

Formatting mirrors the AEMET morning brief so the pushed Telegram messages
look identical in style to the Zaragoza weather report.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import datetime
from typing import Any

log = logging.getLogger("pi02w_hub.mesh")

# Make config importable when this module is run standalone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from meshtastic import tcp_interface
    from meshtastic.protobuf import portnums_pb2, telemetry_pb2

    MESHTASTIC_AVAILABLE = True
except ImportError:
    MESHTASTIC_AVAILABLE = False
    tcp_interface = None  # type: ignore[assignment]
    portnums_pb2 = None  # type: ignore[assignment]
    telemetry_pb2 = None  # type: ignore[assignment]

from config import (  # noqa: E402
    MESH_HOST,
    MESH_PORT,
    MESH_NODE_ID,
    MESH_TELEMETRY_TIMEOUT,
    MESH_CONNECT_TIMEOUT,
    TIMEZONE,
)

# Firmware sentinel for "USB powered / no battery" battery level.
_MAGIC_USB_BATTERY_LEVEL = 0xFFFFFFFF

# Only one request at a time — the bridge proxies a single BLE radio.
_LOCK = threading.Lock()


class MeshWeatherError(Exception):
    """Raised when mesh telemetry cannot be fetched."""


# ---------------------------------------------------------------------------
# Protobuf helpers
# ---------------------------------------------------------------------------


def _normalize_node_id(node_id: str) -> str:
    return node_id.strip().lower()


def _resolve_node_num(iface, node_id: str) -> int | None:
    """Map ``!shortid`` -> numeric node number, or None if not in the map."""
    wanted = _normalize_node_id(node_id)
    nodes = getattr(iface, "nodesByNum", None) or {}
    for num, info in nodes.items():
        if str(info.get("id", "")).lower() == wanted:
            try:
                return int(num)
            except (TypeError, ValueError):
                return None
    return None


def _telemetry_fields(telemetry, kind: str) -> dict[str, Any]:
    """Flatten a telemetry proto into a dict of the fields that are present."""
    fields: dict[str, Any] = {}
    metrics = getattr(telemetry, kind, None)
    if metrics is None:
        return fields
    for attr in _KIND_FIELDS.get(kind, ()):
        if metrics.HasField(attr):
            fields[attr] = getattr(metrics, attr)
    return fields


_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "environment_metrics": (
        "temperature", "relative_humidity", "barometric_pressure",
        "voltage", "current", "iaq", "lux", "white_lux", "ir_lux",
        "uv_lux", "wind_speed", "wind_direction", "wind_gust",
        "rainfall_1h", "rainfall_24h", "radiation", "gas_resistance",
    ),
    "device_metrics": (
        "battery_level", "voltage", "channel_utilization",
        "air_util_tx", "uptime_seconds",
    ),
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class MeshWeather:
    """On-demand Meshtastic telemetry client (single-flight, short-lived)."""

    def __init__(
        self,
        host: str = MESH_HOST,
        port: int = MESH_PORT,
        node_id: str = MESH_NODE_ID,
        timeout: float = MESH_TELEMETRY_TIMEOUT,
        connect_timeout: float = MESH_CONNECT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.node_id = node_id
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    def _connect(self):
        """Open a TCP connection and wait for the node map. Raises on failure."""
        if not MESHTASTIC_AVAILABLE:
            raise MeshWeatherError("meshtastic package not installed")
        if not self.node_id:
            raise MeshWeatherError("MESH_NODE_ID is not set")
        try:
            # TCPInterface's constructor connects and waits for the config /
            # node DB download (waitForConfig). `timeout` bounds that handshake
            # so a dead bridge can't hang a button press for 300s.
            iface = tcp_interface.TCPInterface(
                hostname=self.host,
                portNumber=self.port,
                timeout=self.connect_timeout,
            )
            if iface.nodesByNum is None:
                iface.close()
                raise MeshWeatherError("bridge connected but reported no nodes")
            return iface
        except MeshWeatherError:
            raise
        except Exception as exc:
            raise MeshWeatherError(
                f"cannot connect to mesh bridge ({self.host}:{self.port}): {exc}"
            ) from exc

    def _send_request(self, iface, kind: str, dest: Any) -> None:
        request = telemetry_pb2.Telemetry()
        getattr(request, kind).CopyFrom(
            telemetry_pb2.EnvironmentMetrics()
            if kind == "environment_metrics"
            else telemetry_pb2.DeviceMetrics()
        )
        try:
            iface.sendData(
                request,
                destinationId=dest,
                portNum=portnums_pb2.PortNum.TELEMETRY_APP,
                wantResponse=True,
                onResponse=self._make_response_handler(kind),
            )
        except Exception as exc:
            raise MeshWeatherError(
                f"failed to send {kind} request: {exc}"
            ) from exc

    def fetch(self) -> dict[str, Any]:
        """Fetch environment + device telemetry in one short-lived connection.

        Returns ``{"time", "environment", "device", "snr", "rssi"}``.
        """
        with _LOCK:
            iface = self._connect()
            try:
                result: dict[str, Any] = {
                    "time": 0,
                    "environment": {},
                    "device": {},
                    "snr": None,
                    "rssi": None,
                }
                seen: dict[str, threading.Event] = {
                    "environment_metrics": threading.Event(),
                    "device_metrics": threading.Event(),
                }
                self._seen = seen
                self._result = result
                self._expected_num = _resolve_node_num(iface, self.node_id)
                dest = self._expected_num if self._expected_num is not None else self.node_id

                self._send_request(iface, "environment_metrics", dest)
                env_ok = seen["environment_metrics"].wait(timeout=self.timeout)

                if env_ok:
                    # Best-effort device metrics (battery/uptime) — short wait,
                    # never block the whole request on it.
                    self._send_request(iface, "device_metrics", dest)
                    seen["device_metrics"].wait(timeout=min(self.timeout, 15))

                if not result["environment"] and not result["device"]:
                    raise MeshWeatherError("no telemetry reply from the node")
                return result
            finally:
                self._seen = {}
                self._result = {}
                self._expected_num = None
                try:
                    iface.close()
                except Exception:
                    pass

    def _make_response_handler(self, kind: str):
        """Return a safe response callback that captures one telemetry reply."""

        def on_response(packet: dict) -> None:
            try:
                decoded = packet.get("decoded", {})
                if decoded.get("portnum") != "TELEMETRY_APP":
                    return
                expected = self._expected_num
                if expected is not None and packet.get("from") != expected:
                    return
                telemetry = telemetry_pb2.Telemetry()
                telemetry.ParseFromString(decoded.get("payload", b""))
                if not telemetry.HasField(kind):
                    return
                self._result[kind] = _telemetry_fields(telemetry, kind)
                if telemetry.time:
                    self._result["time"] = telemetry.time
                if packet.get("rxSnr"):
                    self._result["snr"] = packet.get("rxSnr")
                if packet.get("rxRssi"):
                    self._result["rssi"] = packet.get("rxRssi")
                self._seen[kind].set()
            except Exception:
                log.warning("Ignoring malformed telemetry packet", exc_info=True)

        return on_response


_CLIENT = MeshWeather()


# ---------------------------------------------------------------------------
# Formatting (AEMET-style Markdown)
# ---------------------------------------------------------------------------


def _fmt_time(ts: int) -> str:
    if ts:
        try:
            return datetime.fromtimestamp(ts, TIMEZONE).strftime("%H:%M")
        except (OverflowError, OSError, ValueError):
            pass
    return datetime.now().strftime("%H:%M")


def _fmt_uptime(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins}m"
    return f"{mins}m"


def _fmt_signal(snr, rssi) -> str:
    """SNR arrives in 1/4 dB units (0 = unknown); RSSI in dBm."""
    parts = []
    if snr is not None:
        try:
            raw = int(snr)
            if raw != 0:
                parts.append(f"SNR {raw / 4:.1f} dB")
        except (TypeError, ValueError):
            pass
    if rssi is not None:
        try:
            parts.append(f"RSSI {int(rssi)} dBm")
        except (TypeError, ValueError):
            pass
    return " · ".join(parts)


def _fmt_env_short(env: dict[str, Any]) -> str:
    parts = []
    temp = env.get("temperature")
    humidity = env.get("relative_humidity")
    pressure = env.get("barometric_pressure")
    if temp is not None:
        parts.append(f"{temp:.1f}°C")
    if humidity is not None:
        parts.append(f"{humidity:.0f}%")
    if pressure is not None:
        parts.append(f"{pressure:.0f} hPa")
    return " · ".join(parts)


def _fmt_env_extras(env: dict[str, Any]) -> str:
    parts = []
    if env.get("lux") is not None:
        parts.append(f"lux {env['lux']:.0f}")
    if env.get("iaq") is not None:
        parts.append(f"IAQ {env['iaq']:.0f}")
    if env.get("uv_lux") is not None:
        parts.append(f"UV {env['uv_lux']:.0f}")
    if env.get("voltage") is not None:
        parts.append(f"{env['voltage']:.2f} V")
    if env.get("current") is not None:
        parts.append(f"{env['current']:.2f} A")
    if env.get("wind_speed") is not None:
        parts.append(f"wind {env['wind_speed']:.1f} m/s")
    if env.get("wind_gust") is not None:
        parts.append(f"gust {env['wind_gust']:.1f} m/s")
    if env.get("rainfall_1h") is not None:
        parts.append(f"rain 1h {env['rainfall_1h']:.1f} mm")
    return " · ".join(parts)


def _fmt_device(dev: dict[str, Any]) -> str:
    parts = []
    battery = dev.get("battery_level")
    if battery is not None:
        if battery == _MAGIC_USB_BATTERY_LEVEL:
            parts.append("battery USB/charging")
        else:
            parts.append(f"battery {int(battery)}%")
    if dev.get("voltage") is not None:
        parts.append(f"{dev['voltage']:.2f} V")
    if dev.get("channel_utilization") is not None:
        parts.append(f"chan {dev['channel_utilization']:.0f}%")
    if dev.get("air_util_tx") is not None:
        parts.append(f"air tx {dev['air_util_tx']:.1f}%")
    if dev.get("uptime_seconds"):
        parts.append(f"up {_fmt_uptime(int(dev['uptime_seconds']))}")
    return " · ".join(parts)


def _build_report(header: str) -> str | None:
    """Shared report builder used by the morning push and the on-demand button."""
    if not MESH_NODE_ID:
        return None
    try:
        data = _CLIENT.fetch()
    except MeshWeatherError as exc:
        log.warning("Mesh weather fetch failed: %s", exc)
        return None

    lines = [header]
    lines.append(f"  node {MESH_NODE_ID} · LoRa Mesh")
    lines.append(f"  data at {_fmt_time(data['time'])} (live)")
    lines.append("───")
    lines.append("")

    env = data.get("environment", {})
    dev = data.get("device", {})

    env_short = _fmt_env_short(env)
    if env_short:
        lines.append(f"  Now ({_fmt_time(data['time'])}): {env_short}")
        extras = _fmt_env_extras(env)
        if extras:
            lines.append(f"  {extras}")
        lines.append("")

    if dev:
        lines.append("  device")
        lines.append(f"  {_fmt_device(dev)}")

    signal = _fmt_signal(data.get("snr"), data.get("rssi"))
    if signal:
        lines.append(f"  {signal}")

    return "\n".join(lines)


def format_morning() -> str | None:
    """Morning mesh brief — pushed like the AEMET morning report."""
    return _build_report("> Morning — Solar Node")


def format_ondemand() -> str | None:
    """Live mesh weather for the on-demand button."""
    return _build_report("> Solar Node — Mesh")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Print the mesh weather report to stdout."""
    report = format_ondemand()
    if report:
        print(report)
    else:
        print("❌ Mesh telemetry unavailable. Check MESH_NODE_ID and the bridge.")


if __name__ == "__main__":
    main()