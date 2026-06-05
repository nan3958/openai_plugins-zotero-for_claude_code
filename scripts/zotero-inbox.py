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
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_INBOX = Path.home() / "Downloads" / "zotero-inbox"
DONE_SUBDIR = "done"
LOG_FILE = Path.home() / "Library" / "Logs" / "zotero-inbox.log"
ZOTERO_PING = "http://localhost:23119/connector/ping"
CONNECT_TIMEOUT = 3    # seconds — fast fail if Zotero not running
# Pause between imports so Zotero can complete each before the next
INTER_IMPORT_DELAY = 3  # seconds


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
# Finder sort pinning
# ---------------------------------------------------------------------------

# Setting kMDItemDateAdded to a far-future date makes Finder always sort this
# folder to the top when the enclosing folder is sorted by "Date Added".
# plistlib binary format requires a naive datetime (no tzinfo).
_PIN_DATE = datetime(2099, 12, 31)  # naive UTC — plistlib requirement
_DATE_ADDED_ATTR = "com.apple.metadata:kMDItemDateAdded"


def pin_date_added(folder: Path) -> None:
    """Set kMDItemDateAdded to 2099-12-31 so the folder sorts to the top
    of its parent in Finder when sorted by Date Added.

    Uses xattr(1) (macOS built-in) — no extra Python packages required.
    Silently skips if xattr is unavailable or the attribute cannot be set.
    """
    try:
        data = plistlib.dumps(_PIN_DATE, fmt=plistlib.FMT_BINARY)
        subprocess.run(
            ["xattr", "-wx", _DATE_ADDED_ATTR, data.hex(), str(folder)],
            check=True, capture_output=True,
        )
    except Exception:
        pass  # non-fatal — sorting preference, not functional


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
    """Open a PDF with Zotero via macOS `open -a Zotero`.

    Zotero's file handler extracts metadata (DOI, title, authors) from the
    PDF and creates a library item automatically. Returns True if the open
    command succeeds (exit 0); Zotero handles the rest asynchronously.
    """
    try:
        result = subprocess.run(
            ["open", "-a", "Zotero", str(pdf_path)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("Sent to Zotero: %s", pdf_path.name)
            return True
        else:
            logger.warning(
                "open -a Zotero failed (exit %s): %s — %s",
                result.returncode, pdf_path.name, result.stderr.strip(),
            )
            return False
    except Exception as exc:
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

    # Ensure folders exist; pin inbox to top of Downloads by Date Added
    inbox.mkdir(parents=True, exist_ok=True)
    done_dir.mkdir(exist_ok=True)
    pin_date_added(inbox)

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
