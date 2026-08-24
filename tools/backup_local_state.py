"""Create a consistent local backup without copying audio or third-party weights."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BACKUP_ROOT = PROJECT_ROOT.parent / "release_backups"

SQLITE_FILES = (
    "scan_results.db",
    "favorite.db",
    "training_features_checkpoint.db",
)
REGULAR_FILES = (
    "genre_model.pkl",
    "models/active_genre_model.json",
    "models/catalog_embedding_v1.pkl",
    "models/personal_rating_v1.pkl",
    "config.json",
    "librosa_config.json",
    "folder_keywords.json",
    "training_dataset.json",
    "training_dataset.backup.json",
    "genre_review_queue.json",
    "scan_report.json",
    "bad_files.json",
)
EXCLUDED_THIRD_PARTY = (
    "yamnet.onnx",
    "models/discogs_multi_embeddings-effnet-bs64-1.onnx",
    "models/faster-whisper/",
    "runtime/onnx_cuda/",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source), timeout=60) as source_db:
        with sqlite3.connect(str(target), timeout=60) as target_db:
            source_db.backup(target_db, pages=4096, sleep=0.05)
            result = target_db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite integrity_check failed for {source.name}: {result}")


def create_backup(output: Path) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Backup directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    copied = []
    missing = []
    for relative in SQLITE_FILES:
        source = PROJECT_ROOT / relative
        if not source.is_file():
            missing.append(relative)
            continue
        target = output / relative
        _sqlite_backup(source, target)
        copied.append(relative)

    for relative in REGULAR_FILES:
        source = PROJECT_ROOT / relative
        if not source.is_file():
            missing.append(relative)
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative)

    reports = PROJECT_ROOT / "learning_curves"
    if reports.is_dir():
        for source in sorted(reports.iterdir()):
            if not source.is_file():
                continue
            relative = source.relative_to(PROJECT_ROOT).as_posix()
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(relative)

    manifest_files = []
    for relative in sorted(copied):
        target = output / relative
        manifest_files.append({
            "path": relative,
            "size": target.stat().st_size,
            "sha256": _sha256(target),
        })
    manifest = {
        "schema": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "project": "WebMusicPlayer",
        "files": manifest_files,
        "missing_optional": sorted(missing),
        "excluded_third_party_weights": list(EXCLUDED_THIRD_PARTY),
        "contains_audio": False,
    }
    (output / "backup_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path,
        default=DEFAULT_BACKUP_ROOT / f"WebMusicPlayer_{timestamp}",
    )
    args = parser.parse_args()
    manifest = create_backup(args.output)
    total = sum(item["size"] for item in manifest["files"])
    print(json.dumps({
        "status": "ok",
        "output": str(args.output.resolve()),
        "files": len(manifest["files"]),
        "bytes": total,
        "missing_optional": manifest["missing_optional"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

