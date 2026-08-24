"""Build and audit a privacy-safe, model-free GitHub source export."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_PUBLIC_FILE_BYTES = 25 * 1024 * 1024

ROOT_FILES = {
    ".gitignore", "README.md", "LICENSE", "NOTICE.md",
    "config.example.json", "librosa_config.example.json",
    "training_dataset.example.json", "folder_keywords.example.json",
    "yamnet_class_map.csv", "yamnet_genre_map.json",
    "requirements.txt", "requirements-cuda.txt", "requirements-onnx-cpu.txt",
    "run.py", "gui_server.py", "gui_server.spec",
}
TREE_SUFFIXES = {
    "app": {".py"},
    "static": {".css", ".js", ".svg", ".png", ".ico", ".woff", ".woff2"},
    "templates": {".html", ".py"},
    "tests": {".py"},
    "tools": {".py", ".ps1"},
}
DOC_FILES = {
    "docs/DEVELOPMENT_ROADMAP.md",
    "docs/GITHUB_RELEASE.md",
    "docs/MODEL_PIPELINE.md",
    "docs/ONNX_CUDA_RUNTIME.md",
    "docs/THIRD_PARTY_MODELS.md",
}
EXTRA_FILES = {
    "reckordbox_parcer_file_output/analyze_rekordbox_genres.py",
}

FORBIDDEN_NAMES = {
    "config.json", "librosa_config.json", "training_dataset.json",
    "folder_keywords.json",
    "training_dataset.backup.json", "genre_review_queue.json",
    "scan_report.json", "bad_files.json", "info.txt",
    "parsed_rekordbox.json", "uploaded_rekordbox.json",
}
FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".pkl", ".pickle", ".joblib",
    ".onnx", ".pb", ".tflite", ".pt", ".pth", ".safetensors",
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
    ".zip", ".7z", ".log", ".bak",
}
PRIVATE_TEXT_PATTERNS = {
    "private IPv4 address": re.compile(r"\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b"),
    "developer Windows profile": re.compile(r"(?i)(?:[A-Z]:\\Users\\ARTUR|[A-Z]:\\WinUsers\\ARTUR)"),
}


def public_files(root: Path = PROJECT_ROOT):
    for relative in sorted(ROOT_FILES | DOC_FILES | EXTRA_FILES):
        path = root / relative
        if path.is_file():
            yield path
    for directory, suffixes in TREE_SUFFIXES.items():
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path


def audit_tree(root: Path, files=None) -> list[str]:
    errors = []
    candidates = list(files) if files is not None else [p for p in root.rglob("*") if p.is_file()]
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if path.name.lower() in FORBIDDEN_NAMES:
            errors.append(f"forbidden personal file: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden binary/data suffix: {relative}")
        size = path.stat().st_size
        if size > MAX_PUBLIC_FILE_BYTES:
            errors.append(f"file exceeds 25 MiB: {relative} ({size} bytes)")
        if size > 4 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PRIVATE_TEXT_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {relative}")
    return sorted(set(errors))


def build_export(output: Path, *, init_git: bool = False) -> dict:
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in public_files(PROJECT_ROOT):
        relative = source.relative_to(PROJECT_ROOT)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative.as_posix())
    errors = audit_tree(output)
    if errors:
        raise RuntimeError("Release audit failed:\n- " + "\n- ".join(errors))
    if init_git:
        subprocess.run(["git", "init", "--initial-branch", "main"], cwd=output, check=True)
        subprocess.run(["git", "add", "--all"], cwd=output, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Prepare privacy-safe Web Music Player source release"],
            cwd=output, check=True,
        )
    return {"output": str(output), "files": len(copied), "audit_errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--init-git", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if args.audit_only:
        files = list(public_files(PROJECT_ROOT))
        errors = audit_tree(PROJECT_ROOT, files)
        result = {"status": "ok" if not errors else "error", "files": len(files), "errors": errors}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if args.output is None:
        parser.error("--output is required unless --audit-only is used")
    print(json.dumps(build_export(args.output, init_git=args.init_git), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
