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


def _lp_available() -> bool:
    try:
        result = subprocess.run(["which", "lp"], capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _print_via_lp(path: Path, printer_name: str,
                  color: bool = True, duplex: bool = False) -> tuple[bool, str]:
    try:
        cmd = ["lp", "-d", printer_name]
        if not color:
            cmd.extend(["-o", "ColorModel=Gray"])
        if duplex:
            cmd.extend(["-o", "sides=two-sided-long-edge"])
        else:
            cmd.extend(["-o", "sides=one-sided"])
        cmd.append(str(path))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_SEND_TIMEOUT,
        )
        if result.returncode == 0:
            log.info("Printed %s via CUPS queue %s", path.name, printer_name)
            return True, f"Sent to printer ({path.name})"
        err = result.stderr.strip() or "lp command failed"
        return False, err
    except FileNotFoundError:
        return False, "lp command not found (CUPS not installed?)"
    except subprocess.TimeoutExpired:
        return False, "lp timed out"
    except OSError as e:
        return False, f"lp error: {e}"


def _print_via_raw(path: Path, ip: str) -> tuple[bool, str]:
    try:
        with socket.create_connection((ip, _PRINTER_PORT), timeout=_SEND_TIMEOUT) as sock:
            sock.sendall(b"\x1b%-12345X@PJL JOB\n@PJL ENTER LANGUAGE=PDF\n")
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            sock.sendall(b"\n@PJL EOJ\n\x1b%-12345X")
        log.info("Sent %s raw to %s", path.name, ip)
        return True, f"Sent to printer ({path.name})"
    except socket.timeout:
        return False, "Printer did not respond (timeout)"
    except ConnectionRefusedError:
        return False, "Printer refused the connection"
    except socket.gaierror:
        return False, f"Printer address {ip} is unreachable"
    except OSError as e:
        return False, f"Network error: {e}"


def print_pdf(path: Path, addr: str, printer_name: str = "",
              color: bool = True, duplex: bool = False) -> tuple[bool, str]:
    if not path.is_file():
        return False, "File not found"
    size = path.stat().st_size
    if size == 0:
        return False, "File is empty"
    if size > 50 * 1024 * 1024:
        return False, "File exceeds 50 MB limit"

    if _lp_available() and printer_name:
        return _print_via_lp(path, printer_name, color=color, duplex=duplex)

    ip = resolve_addr(addr) if addr else None
    if not ip:
        return False, (
            "Printer not found. Install CUPS and run:\n"
            f"  sudo lpadmin -p HP_Envy_6400 -v ipp://{addr}/ipp/print -E -m everywhere\n"
            "Then set PRINTER_NAME in .env"
        )

    return _print_via_raw(path, ip)

