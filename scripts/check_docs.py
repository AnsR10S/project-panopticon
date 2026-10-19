"""Check the doc links and make sure the original files still match their hashes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from auditors_paradox.cli import verify_sources


def main() -> int:
    """Go through the active links and verify the preserved source files."""
    root = Path(__file__).resolve().parents[1]
    files = [root / "README.md"]
    files += [
        p
        for folder in ("docs", "paper", "notebooks", "analysis", "runs", "external")
        for p in (root / folder).rglob("*.md")
        if ("source-materials" not in p.parts or p == root / "docs/source-materials/README.md")
        and "evidence" not in p.parts
        and "Critique" not in p.parts
        and not p.is_relative_to(root / "docs/publication/local")
        and (folder not in {"runs", "analysis"} or p == root / folder / "README.md")
        and "collusion-lab" not in p.parts
        and ".cache" not in p.parts
    ]
    errors = []
    checked = 0
    for path in files:
        markdown = re.sub(
            r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$",
            "",
            path.read_text(encoding="utf-8"),
        )
        for target in re.findall(r"\]\(([^)]+)\)", markdown):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                continue
            local = unquote(target.split("#", 1)[0].strip("<>"))
            if local:
                checked += 1
                if not (path.parent / local).exists():
                    errors.append(f"{path.relative_to(root)} -> {target}")
    sources = verify_sources(root)
    result = {"checked_links": checked, "broken_links": errors, "sources": sources}
    print(json.dumps(result, indent=2))
    return 1 if errors or sources["valid"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
