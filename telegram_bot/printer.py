"""Print PDFs to an HP PCL3 inkjet (Envy/DeskJet) over raw JetDirect port 9100.

These printers have no PDF interpreter on port 9100 — a raw PDF prints as
gibberish — so we render the PDF to PCL3 with ghostscript and send PCL.
"""

from __future__ import annotations

import logging
import socket
import subprocess
from pathlib import Path

log = logging.getLogger("aihub.printer")

_PRINTER_PORT = 9100
_SEND_TIMEOUT = 30
_CHUNK_SIZE = 65536


def _to_pcl(path: Path) -> Path:
    """Render a PDF to a sibling ``.pcl`` file using ghostscript's pcl3 device."""
    out = path.with_suffix(".pcl")
    subprocess.run(
        [
            "gs", "-q", "-dNOPAUSE", "-dBATCH",
            "-sDEVICE=pcl3",
            f"-sOutputFile={out}",
            str(path),
        ],
        check=True, capture_output=True, text=True, timeout=_SEND_TIMEOUT * 4,
    )
    return out


def print_pdf(path: Path, addr: str,
              color: bool = True, duplex: bool = False) -> tuple[bool, str]:
    if not path.is_file():
        return False, "File not found"
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False, "File is not a valid PDF"
    except OSError as e:
        return False, f"Cannot read file: {e}"

    if not addr:
        return False, "Set PRINTER_ADDR in .env (the printer's IP)"

    try:
        pcl = _to_pcl(path)
    except FileNotFoundError:
        return False, "Ghostscript not found. Install it:  sudo apt install ghostscript"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False, "Failed to render PDF to PCL"

    header = (
        b"\x1b%-12345X@PJL JOB NAME=\"telegram-print\"\n"
        + (b"@PJL SET DUPLEX=ON\n" if duplex else b"@PJL SET DUPLEX=OFF\n")
        + (b"@PJL SET RENDERMODE=GRAYSCALE\n" if not color else b"@PJL SET RENDERMODE=AUTOCOLOR\n")
        + b"@PJL ENTER LANGUAGE=PCL\n"
    )

    try:
        with socket.create_connection((addr, _PRINTER_PORT), timeout=_SEND_TIMEOUT) as sock:
            sock.sendall(header)
            with open(pcl, "rb") as f:
                while True:
                    chunk = f.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
            sock.sendall(b"\x1b%-12345X@PJL EOJ\n\x1b%-12345X")
        log.info("Printed %s", path.name)
        return True, f"Sent to printer ({path.name})"
    except socket.timeout:
        return False, "Printer did not respond (timeout)"
    except ConnectionRefusedError:
        return False, "Printer refused the connection"
    except OSError as e:
        return False, f"Network error: {e}"
    finally:
        pcl.unlink(missing_ok=True)
