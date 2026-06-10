from __future__ import annotations

import logging
import socket
import subprocess
from pathlib import Path

log = logging.getLogger("aihub.printer")

_PRINTER_PORT = 9100
_SEND_TIMEOUT = 30
_CHUNK_SIZE = 65536


def _resolve(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return None


def _discover_via_avahi() -> str | None:
    try:
        result = subprocess.run(
            ["avahi-browse", "-t", "_ipp._tcp", "-p"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "ENVY" in line.upper() or "HP" in line.upper():
                parts = line.split(";")
                if len(parts) >= 8 and parts[7]:
                    return parts[7]
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        pass
    return None


def resolve_addr(addr: str) -> str | None:
    if not addr:
        return None
    if _is_ip(addr):
        return addr
    resolved = _resolve(addr)
    if resolved:
        return resolved
    discovered = _discover_via_avahi()
    if discovered:
        return discovered
    return None


def _is_ip(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def print_pdf(path: Path, addr: str) -> tuple[bool, str]:
    if not path.is_file():
        return False, "File not found"
    size = path.stat().st_size
    if size == 0:
        return False, "File is empty"
    if size > 50 * 1024 * 1024:
        return False, "File exceeds 50 MB limit"

    ip = resolve_addr(addr)
    if not ip:
        return False, (
            "Printer not found. Set PRINTER_ADDR in .env\n"
            "(e.g. PRINTER_ADDR=192.168.1.x) and restart the bot."
        )

    host_label = f"{addr} ({ip})" if addr != ip else ip
    try:
        with socket.create_connection((ip, _PRINTER_PORT), timeout=_SEND_TIMEOUT) as sock:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
        log.info("Sent %s (%d B) to %s", path.name, size, host_label)
        return True, f"Sent to printer ({path.name})"
    except socket.timeout:
        return False, "Printer did not respond (timeout)"
    except ConnectionRefusedError:
        return False, "Printer refused the connection"
    except socket.gaierror:
        return False, f"Printer address {addr} is unreachable"
    except OSError as e:
        return False, f"Network error: {e}"
