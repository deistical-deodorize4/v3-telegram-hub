"""Print PDFs to an HP PCL3 inkjet (Envy/DeskJet) over raw JetDirect port 9100.

These printers have no PDF interpreter on port 9100 — a raw PDF prints as
gibberish — so we render the PDF to PCL3 with ghostscript and send PCL.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("aihub.printer")

_PRINTER_PORT = 9100
_SEND_TIMEOUT = 30
_CHUNK_SIZE = 65536
# Hard safety cap: a user uploading a 250+ page PDF would take forever on a Pi.
_PAGE_CAP = 250
# Longest side (px) after downscaling an image; keeps PCL rendering fast on a Pi.
_IMAGE_SAVE_CAP = 2000
_IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
}


def _to_pcl(path: Path) -> Path:
    """Render a PDF to a sibling ``.pcl`` file using ghostscript's pcl3 device.

    The gs ``pcl3`` device has a bug where multi-page PDFs come out blank, so
    we render every page separately (``-dFirstPage``/``-dLastPage``) and
    concatenate the per-page PCL into one stream. Ghostscript exits without
    creating an output file once ``-dFirstPage`` is past the last page, which
    is how we detect the end of the document.
    """
    out = path.with_suffix(".pcl")
    tmpdir = Path(tempfile.mkdtemp(prefix="pcl-"))
    try:
        pages = 0
        with out.open("wb") as dst:
            for page in range(1, _PAGE_CAP + 1):
                page_pcl = tmpdir / f"page-{page}.pcl"
                subprocess.run(
                    [
                        "gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                        f"-dFirstPage={page}", f"-dLastPage={page}",
                        "-sDEVICE=pcl3",
                        f"-sOutputFile={page_pcl}",
                        str(path),
                    ],
                    capture_output=True, text=True, timeout=_SEND_TIMEOUT * 4,
                )
                if not page_pcl.is_file() or page_pcl.stat().st_size == 0:
                    if page == 1:
                        raise subprocess.CalledProcessError(
                            1, "gs", output="", stderr="page 1 produced no output"
                        )
                    break  # past the last page
                dst.write(page_pcl.read_bytes())
                pages += 1
        if pages == 0:
            raise subprocess.CalledProcessError(1, "gs", output="", stderr="no pages rendered")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def _image_to_pdf(path: Path) -> Path:
    """Rasterize an image to a sibling ``.pdf`` file via Pillow.

    The result drops into the existing PDF → PCL pipeline unchanged. EXIF
    rotation is applied, transparency is flattened onto white, and very large
    photos are downscaled so PCL rendering stays quick on a Pi.
    """
    out = path.with_suffix(".pdf")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise RuntimeError(
            "Pillow is not installed. Install it:  sudo python3 -m pip install Pillow"
        ) from None
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        if max(im.size) > _IMAGE_SAVE_CAP:
            im.thumbnail((_IMAGE_SAVE_CAP, _IMAGE_SAVE_CAP))
        im.save(out, "PDF", resolution=200.0)
    return out


def print_file(path: Path, addr: str,
               color: bool = True, duplex: bool = False) -> tuple[bool, str]:
    """Print a PDF, or an image (rasterised to PDF first), to the printer."""
    if not path.is_file():
        return False, "File not found"
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        try:
            pdf = _image_to_pdf(path)
        except Exception as e:
            return False, f"Unsupported or unreadable image: {e}"
        try:
            return print_pdf(pdf, addr, color=color, duplex=duplex)
        finally:
            pdf.unlink(missing_ok=True)
    return print_pdf(path, addr, color=color, duplex=duplex)


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

    if not addr or not addr.strip():
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
