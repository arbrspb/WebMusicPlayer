"""Audit Rekordbox classes and path availability before Stage 2 training."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.config import load_config  # noqa: E402
from app.paths import LEARNING_CURVES_DIR, REKORDBOX_OUTPUT_DIR, resolve_mapped_music_path  # noqa: E402
from app.track_taxonomy import parse_track_taxonomy  # noqa: E402


TARGET_STYLES = ("Deep House", "Moombahton", "Disco", "Pop", "Tech House")


def _audit_row(row, music_dir):
    raw_genre = str(row.get("Genre") or row.get("genre") or "").strip()
    if not raw_genre:
        return None
    source_path = str(row.get("path") or row.get("Path") or "")
    resolved_path = resolve_mapped_music_path(source_path, music_dir)
    taxonomy = parse_track_taxonomy(
        raw_genre=raw_genre,
        title=row.get("Title") or row.get("title") or "",
        artist=row.get("Artist") or row.get("artist") or "",
        path=resolved_path,
    )
    return taxonomy.base_genre, bool(resolved_path and os.path.isfile(resolved_path))


def build_report(export_path, min_tracks=80, workers=32):
    with Path(export_path).open("r", encoding="utf-8") as export_file:
        rows = json.load(export_file)
    music_dir = load_config().get("music_dir")
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        audited = list(executor.map(
            lambda row: _audit_row(row, music_dir),
            rows,
            chunksize=128,
        ))
    labelled_counts = Counter()
    accessible_counts = Counter()
    for result in audited:
        if result is None:
            continue
        base_genre, accessible = result
        labelled_counts[base_genre] += 1
        if accessible:
            accessible_counts[base_genre] += 1
    readiness = {
        style: {
            "labelled": labelled_counts[style],
            "accessible": accessible_counts[style],
            "ready": accessible_counts[style] >= int(min_tracks),
        }
        for style in TARGET_STYLES
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "export_path": str(Path(export_path).resolve()),
        "music_dir": music_dir,
        "minimum_tracks": int(min_tracks),
        "total_export_rows": len(rows),
        "labelled_rows": sum(labelled_counts.values()),
        "accessible_labelled_rows": sum(accessible_counts.values()),
        "styles": readiness,
        "ready_styles": [style for style, values in readiness.items() if values["ready"]],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--export",
        default=str(REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json"),
    )
    parser.add_argument("--minimum", type=int, default=80)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument(
        "--output",
        default=str(LEARNING_CURVES_DIR / "stage2_readiness.json"),
    )
    args = parser.parse_args()
    report = build_report(args.export, min_tracks=args.minimum, workers=args.workers)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
