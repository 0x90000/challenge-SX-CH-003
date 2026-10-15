"""Package only explicitly selected project files and verified evidence."""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psrc.codec import read_json  # noqa: E402


def source_files():
    selected = []
    for directory in (
        "psrc",
        "examples",
        "data",
        "configs",
        "schemas",
        "tests",
        "scripts",
        "docs",
        ".github",
    ):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                selected.append((path, path.relative_to(ROOT).as_posix()))
    for name in (
        "README.md",
        "NOTICE.md",
        "Dockerfile",
        ".dockerignore",
        ".gitignore",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
    ):
        selected.append((ROOT / name, name))
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/SX-CH-003-submission.zip")
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    verification = read_json(evidence / "verification.json")
    if verification["status"] != "success":
        parser.error("Evidence did not pass verification")
    selected = source_files()
    current = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in selected}
    if current != read_json(evidence / "source-sha256.json"):
        parser.error("Source files differ from the verified snapshot; regenerate evidence")
    output = args.output.resolve()
    if output.exists():
        parser.error("Refusing to overwrite an existing submission package")
    for path in sorted(evidence.rglob("*")):
        if path.is_file():
            selected.append((path, "evidence/" + path.relative_to(evidence).as_posix()))
    for path, name in selected:
        if path.is_symlink() or not path.resolve().is_relative_to(
            evidence if name.startswith("evidence/") else ROOT
        ):
            parser.error(f"External file or symbolic link is not allowed: {path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in selected:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None, "Archive CRC verification failed"
        assert len(archive.namelist()) == len(selected), "Archive file count differs"
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n", encoding="ascii")
    print(f"{output}\nSHA256: {checksum}\nFiles: {len(selected)}")


if __name__ == "__main__":
    main()
