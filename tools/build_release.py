#!/usr/bin/env python3
"""Build and verify a deterministic philosophical-guide release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "philosophical-guide"
CHECKSUM_PATH = REPO_ROOT / "SHA256SUMS.txt"
ARCHIVE_PREFIX = "philosophical-guide-roadmap-v"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REQUIRED_FILES = {
    "SKILL.md",
    "LICENSE.txt",
    "agents/openai.yaml",
    "references/diagnostic-playbook.md",
    "references/evaluation-evidence.md",
    "references/example-roadmap.json",
    "references/roadmap-workflow.md",
    "references/thought-roadmap-schema.md",
    "scripts/render_thought_roadmap.py",
}


class ReleaseError(ValueError):
    pass


def skill_version() -> str:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'^\s+version:\s*"([^"]+)"\s*$', content, re.MULTILINE)
    if match is None:
        raise ReleaseError("SKILL.md metadata.version was not found")
    version = match.group(1)
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(f"unsupported version syntax in SKILL.md: {version!r}")
    return version


def package_files() -> list[Path]:
    symlinks = sorted(
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_symlink()
    )
    if symlinks:
        raise ReleaseError("symbolic links must not enter the release archive: " + ", ".join(symlinks))
    files = sorted(
        (
            path
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        ),
        key=lambda path: path.relative_to(SKILL_ROOT).as_posix(),
    )
    relatives = {path.relative_to(SKILL_ROOT).as_posix() for path in files}
    missing = sorted(REQUIRED_FILES - relatives)
    if missing:
        raise ReleaseError("required package files are missing: " + ", ".join(missing))
    hidden = sorted(
        relative
        for relative in relatives
        if any(part.startswith(".") for part in PurePosixPath(relative).parts)
    )
    if hidden:
        raise ReleaseError("hidden files must not enter the release archive: " + ", ".join(hidden))
    return files


def archive_name(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ReleaseError(f"unsupported version syntax: {version!r}")
    return f"{ARCHIVE_PREFIX}{version}.zip"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def write_archive(path: Path, files: list[Path]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temp_path,
            "w",
            compression=zipfile.ZIP_STORED,
        ) as archive:
            for source in files:
                relative = source.relative_to(SKILL_ROOT).as_posix()
                member = f"philosophical-guide/{relative}"
                info = zipfile.ZipInfo(member, FIXED_TIMESTAMP)
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.flag_bits |= 0x800
                archive.writestr(
                    info,
                    source.read_bytes(),
                    compress_type=zipfile.ZIP_STORED,
                )
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def verify_archive(path: Path, files: list[Path]) -> dict[str, object]:
    expected = {
        f"philosophical-guide/{source.relative_to(SKILL_ROOT).as_posix()}": source.read_bytes()
        for source in files
    }
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ReleaseError("archive contains duplicate member names")
        if names != sorted(expected):
            raise ReleaseError("archive members differ from the sorted package source inventory")
        if archive.testzip() is not None:
            raise ReleaseError("archive CRC verification failed")
        for info in archive.infolist():
            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or ".." in pure.parts or pure.parts[0] != "philosophical-guide":
                raise ReleaseError(f"unsafe archive member: {info.filename}")
            if info.file_size > 10 * 1024 * 1024:
                raise ReleaseError(f"archive member is unexpectedly large: {info.filename}")
            if archive.read(info) != expected[info.filename]:
                raise ReleaseError(f"archive member differs from source: {info.filename}")
    return {
        "archive": str(path.resolve()),
        "sha256": sha256_file(path),
        "members": len(expected),
        "source_match": True,
    }


def update_checksums(filename: str, digest: str) -> None:
    lines = CHECKSUM_PATH.read_text(encoding="utf-8").splitlines() if CHECKSUM_PATH.exists() else []
    kept = [line for line in lines if not line.endswith(f"  {filename}")]
    kept.append(f"{digest}  {filename}")
    CHECKSUM_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build, verify, and checksum the current Skill")
    build.add_argument("--version", help="Expected version; defaults to SKILL.md metadata.version")
    build.add_argument("--output-dir", type=Path, default=REPO_ROOT)
    verify = subparsers.add_parser("verify", help="Verify an existing archive against current source")
    verify.add_argument("--archive", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        files = package_files()
        if args.command == "build":
            detected = skill_version()
            version = args.version or detected
            if version != detected:
                raise ReleaseError(
                    f"requested version {version!r} does not match SKILL.md metadata.version {detected!r}"
                )
            args.output_dir.mkdir(parents=True, exist_ok=True)
            output = args.output_dir / archive_name(version)
            write_archive(output, files)
            report = verify_archive(output, files)
            update_checksums(output.name, str(report["sha256"]))
            report["version"] = version
        else:
            report = verify_archive(args.archive, files)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, zipfile.BadZipFile, ReleaseError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
