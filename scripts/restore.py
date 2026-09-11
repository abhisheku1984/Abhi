"""Restore a backup archive created by scripts/backup.py.

Usage:
    python scripts/restore.py <archive.zip> [--confirm]
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# Where each in-archive prefix lands on disk.
TARGETS = {
    "database/studio.db": BACKEND / "data" / "studio.db",
    "storage/": BACKEND / "storage",
    "config/backend.env": BACKEND / ".env",
    "logs/": BACKEND / "data" / "logs",
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/restore.py <archive.zip> [--confirm]")
        return 2
    archive = Path(sys.argv[1])
    confirm = "--confirm" in sys.argv
    if not archive.exists():
        print(f"Archive not found: {archive}")
        return 1

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else {}
        print(f"Backup from {manifest.get('created_at', 'unknown')}")
        print(f"Database: {manifest.get('database_bytes', 0)} bytes")
        print(f"Assets:   {manifest.get('storage_bytes', 0)} bytes")
        if not confirm:
            print("\nThis OVERWRITES the current database, storage and .env.")
            print("Re-run with --confirm to proceed.")
            return 0

        for name in names:
            if name == "manifest.json":
                continue
            target = None
            for prefix, destination in TARGETS.items():
                if name == prefix or name.startswith(prefix):
                    remainder = name[len(prefix):] if name.startswith(prefix) else ""
                    target = destination / remainder if remainder else destination
                    break
            if target is None or name.startswith("docs/"):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    print(f"\nRestore complete from {archive.name}.")
    print("Start the platform with scripts/START to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
