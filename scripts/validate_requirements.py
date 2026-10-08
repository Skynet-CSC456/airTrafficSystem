"""Check requirement documents against the index, source, and tests."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SUBSYSTEMS = ("radar", "tower", "command", "airport")
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".py", ".java", ".go", ".c", ".cpp", ".h", ".hpp"}
TEST_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".py"}


def references(directory: Path, requirement_id: str, suffixes: set[str]) -> list[Path]:
    return [
        path for path in directory.rglob("*")
        if path.is_file() and path.suffix in suffixes
        and requirement_id in path.read_text(encoding="utf-8", errors="replace")
    ]


def validate(subsystem: str) -> int:
    requirements_dir = ROOT / "requirements" / subsystem
    source_dir = ROOT / "src" / subsystem
    tests_dir = ROOT / "tests" / subsystem
    index = ROOT / "docs" / "requirements-index.md"
    missing = [path for path in (requirements_dir, source_dir, tests_dir, index) if not path.exists()]
    if missing:
        for path in missing:
            print(f"Missing required path: {path.relative_to(ROOT)}")
        return 1

    documents = sorted(requirements_dir.rglob("REQ-*.md"))
    if not documents:
        print(f"No requirement files found in {requirements_dir.relative_to(ROOT)}")
        return 1

    index_text = index.read_text(encoding="utf-8")
    errors = 0
    for document in documents:
        match = re.search(r"REQ-[A-Z]+-[0-9]+", document.name)
        if match is None:
            print(f"Invalid requirement filename: {document.relative_to(ROOT)}")
            errors += 1
            continue
        requirement_id = match.group()
        checks = {
            "index": re.search(rf"^\|\s*{re.escape(requirement_id)}\s*\|", index_text, re.MULTILINE) is not None,
            "source": bool(references(source_dir, requirement_id, SOURCE_SUFFIXES)),
            "tests": bool(references(tests_dir, requirement_id, TEST_SUFFIXES)),
        }
        print(f"{requirement_id}: " + ", ".join(f"{name}={'OK' if passed else 'MISSING'}" for name, passed in checks.items()))
        errors += sum(not passed for passed in checks.values())

    if errors:
        print(f"Validation failed with {errors} error(s).")
        return 1
    print(f"Validation passed for {subsystem}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subsystem", choices=SUBSYSTEMS)
    args = parser.parse_args()
    raise SystemExit(validate(args.subsystem))
