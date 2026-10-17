"""Load the fixed notice bodies and the visibility headers."""

from __future__ import annotations

from pathlib import Path

from auditors_paradox.config import StudyConfig


def feedback_paths(cfg: StudyConfig | None = None) -> dict[str, str]:
    """Keep the selected template's file identity alongside the rendered text."""
    names = {name: name for name in ("warning", "neutral", "private", "public")}
    if cfg and cfg.v2:
        names.update(warning=cfg.v2.warning_template, neutral=cfg.v2.placebo_template)
    return {name: f"prompts/feedback/{filename}.txt" for name, filename in names.items()}


def load_feedback(root: Path, cfg: StudyConfig | None = None) -> dict[str, str]:
    """Read the warning, neutral, private and public prompt files."""
    return {
        name: (root / path).read_text(encoding="utf-8").strip()
        for name, path in feedback_paths(cfg).items()
    }
