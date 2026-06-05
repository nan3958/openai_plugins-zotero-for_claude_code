#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests>=2.31",
# ]
# ///
"""Watch a folder for PDFs and import them into a running Zotero instance.

Designed to be triggered by launchd WatchPaths — run once per folder change,
not as a persistent process. Scans the inbox folder for PDFs, imports each
into Zotero via the local connector API (localhost:23119), then moves
successfully imported files to an 'done/' subfolder.

Usage:
    uv run scripts/zotero-inbox.py [INBOX_FOLDER]

    INBOX_FOLDER defaults to ~/Downloads/zotero-inbox

Setup (one-time):
    1. Create the inbox folder:
           mkdir -p ~/Downloads/zotero-inbox
    2. Install the launchd job (see ../launchd/com.user.zotero-inbox.plist).
    3. Drop PDFs into the inbox folder. Zotero must be open to receive them.

Logs are written to ~/Library/Logs/zotero-inbox.log.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_INBOX = Path.home() / "Downloads" / "zotero-inbox"
DONE_SUBDIR = "done"
LOG_FILE = Path.home() / "Library" / "Logs" / "zotero-inbox.log"
ZOTERO_PING = "http://localhost:23119/connector/ping"
ZOTERO_IMPORT = "http://localhost:23119/connector/import"
CONNECT_TIMEOUT = 3    # seconds — fast fail if Zotero not running
IMPORT_TIMEOUT = 30    # seconds — allow time for metadata retrieval
# Brief pause between imports so Zotero can complete each before the next
INTER_IMPORT_DELAY = 2  # seconds


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("zotero-inbox")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    # File handler (persistent log)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # Stderr handler (visible in launchd output log)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


# ---------------------------------------------------------------------------
# Zotero API
# ---------------------------------------------------------------------------

def zotero_is_running() -> bool:
    """Return True if Zotero's connector server is reachable."""
    try:
        r = requests.get(ZOTERO_PING, timeout=CONNECT_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def import_pdf(pdf_path: Path, logger: logging.Logger) -> bool:
    """POST a PDF to Zotero's local connector import endpoint.

    Returns True on success (HTTP 200/201), False otherwise.
    Zotero extracts metadata (DOI, title, authors) from the PDF automatically.
    """
    try:
        with open(pdf_path, "rb") as fh:
            data = fh.read()
        resp = requests.post(
            ZOTERO_IMPORT,
            headers={"Content-Type": "application/pdf"},
            data=data,
            timeout=IMPORT_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            logger.info("Imported: %s", pdf_path.name)
            return True
        else:
            logger.warning("Import failed (HTTP %s): %s", resp.status_code, pdf_path.name)
            return False
    except requests.RequestException as exc:
        logger.error("Import error for %s: %s", pdf_path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "inbox",
        nargs="?",
        type=Path,
        default=DEFAULT_INBOX,
        help=f"Inbox folder to watch (default: {DEFAULT_INBOX})",
    )
    args = parser.parse_args()

    inbox: Path = args.inbox.expanduser().resolve()
    done_dir: Path = inbox / DONE_SUBDIR

    logger = _setup_logging()
    logger.info("--- zotero-inbox triggered, scanning: %s", inbox)

    # Ensure folders exist
    inbox.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(exist_ok=True)

    # Collect PDFs directly in inbox (not recursing into done/ or subdirs)
    pdfs = sorted(
        p for p in inbox.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )

    if not pdfs:
        logger.info("No PDFs found — nothing to do.")
        return 0

    logger.info("Found %d PDF(s) to process.", len(pdfs))

    if not zotero_is_running():
        logger.warning(
            "Zotero is not running (localhost:23119 unreachable). "
            "Open Zotero and re-trigger by touching the inbox folder, "
            "or run this script manually."
        )
        return 1

    n_ok = n_fail = 0
    for pdf in pdfs:
        if import_pdf(pdf, logger):
            dest = done_dir / pdf.name
            # Avoid overwriting if a file with the same name already exists
            if dest.exists():
                stem, suffix = pdf.stem, pdf.suffix
                dest = done_dir / f"{stem}_{int(time.time())}{suffix}"
            shutil.move(str(pdf), dest)
            logger.info("Moved to done/: %s → %s", pdf.name, dest.name)
            n_ok += 1
            if len(pdfs) > 1:
                time.sleep(INTER_IMPORT_DELAY)
        else:
            n_fail += 1

    logger.info("Done. %d imported, %d failed.", n_ok, n_fail)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
