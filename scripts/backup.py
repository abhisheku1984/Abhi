"""Create a restorable archive of the studio's local state.

Backs up the database, generated assets, configuration and logs — everything
the platform needs to come back exactly as it was.

Usage:
    python scripts/backup.py [output_dir]      # output_dir defaults to ./backups
"""

from __future__ import annotations

import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

INCLUDES = [
    (BACKEND / "data" / "studio.db", "database/studio.db"),
    (BACKEND / "storage", "storage"),
    (BACKEND / ".env", "config/backend.env"),
    (BACKEND / "data" / "logs", "logs"),
    (ROOT / "docs", "docs"),
]

MANIFEST = "manifest.json"


def build_manifest() -> dict:
    db = BACKEND / "data" / "studio.db"
    storage = BACKEND / "storage"
    size = sum(f.stat().st_size for f in storage.rglob("*") if f.is_file()) if storage.exists() else 0
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "platform": sys.platform,
        "database_bytes": db.stat().st_size if db.exists() else 0,
        "storage_bytes": size,
        "storage_kind": "local",
        "includes": [str(src.relative_to(ROOT)) for src, _ in INCLUDES if src.exists()],
    }


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"studio-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"

    import json

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST, json.dumps(build_manifest(), indent=2))
        for src, target in INCLUDES:
            if not src.exists():
                continue
            if src.is_file():
                zf.write(src, target)
            else:
                for file in src.rglob("*"):
                    if file.is_file():
                        zf.write(file, f"{target}/{file.relative_to(src)}")

    print(f"Backup written to {archive_path} ({archive_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
