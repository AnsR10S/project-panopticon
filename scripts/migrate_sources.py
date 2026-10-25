"""Move the original materials to the places the docs say they should be."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs" / "source-materials"


def main() -> None:
    """Check the original hashes, move the files, then make sure they landed right."""
    inventory = json.loads(
        (ROOT / "docs/reviews/2026-10-16/evidence/source-inventory.json").read_text()
    )
    mapping = []
    for row in inventory["files"]:
        old = Path(row["path"])
        if old.parts[0] == "Auditors_Paradox_Complete_Research_Package":
            new = Path("research-package-v1").joinpath(*old.parts[1:])
        elif old.suffix == ".zip":
            new = Path("archives") / old.name
        elif old.suffix == ".png":
            new = Path("diagram-drafts") / old.name
        else:
            new = Path(old.name)
        target = DEST / new
        if not target.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Migration target escaped workspace")
        source = ROOT / old
        current = source if source.exists() else target
        if hashlib.sha256(current.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"Source changed: {old}")
        if source.exists() and target.exists():
            raise FileExistsError(target)
        mapping.append(
            {
                **row,
                "original_path": old.as_posix(),
                "current_path": target.relative_to(ROOT).as_posix(),
            }
        )
    DEST.mkdir(parents=True, exist_ok=True)
    moves = [
        (ROOT / "Chat_original.md", DEST / "Chat_original.md"),
        (ROOT / "Auditors_Paradox_Complete_Research_Package", DEST / "research-package-v1"),
    ]
    moves += [
        (ROOT / row["original_path"], ROOT / row["current_path"])
        for row in mapping
        if Path(row["original_path"]).suffix in {".png", ".zip"} and "/" not in row["original_path"]
    ]
    for source, target in moves:
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
    for row in mapping:
        if hashlib.sha256((ROOT / row["current_path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise RuntimeError(f"Migration verification failed: {row['original_path']}")
    manifest = {
        "schema_version": 1,
        "migrated_on": "2026-10-16",
        "files_removed": 0,
        "file_count": len(mapping),
        "files": mapping,
    }
    (DEST / "source-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Verified {len(mapping)} preserved originals; files_removed = 0")


if __name__ == "__main__":
    main()
