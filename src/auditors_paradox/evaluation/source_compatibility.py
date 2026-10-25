"""Check an explicit exploratory exception for unused pricing-template drift."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from auditors_paradox.config import StudyConfig
from auditors_paradox.evidence import fingerprint
from auditors_paradox.pricing import pricing_templates


def review_unused_prompt_drift(paths: list[Path], root: Path) -> dict[str, Any]:
    """Require identical active inputs and code; never execute archived source."""
    report: dict[str, Any] = {"accepted": False, "policy": "exploratory_unused_pricing_templates_only"}
    manifests = [json.loads(path.read_text(encoding="utf8")) for path in paths]
    if not manifests:
        return {**report, "reason": "No validated runs to compare"}
    if any(m["config"]["run_kind"] == "confirmatory" for m in manifests):
        return {**report, "reason": "Confirmatory studies require their original source identity"}
    reference = manifests[0]
    fields = ("pricing_templates", "feedback_texts", "feedback_template_paths", "protocol_sha256",
              "upstream_commit", "benchmarks", "data_class", "enforcement")
    for key in fields:
        if any(m.get(key) != reference.get(key) for m in manifests):
            return {**report, "reason": f"Active input differs: {key}"}
    configs = [{k: v for k, v in m["config"].items() if k not in {"arm", "seed", "run_kind", "study_id"}}
               for m in manifests]
    if any(cfg != configs[0] for cfg in configs):
        return {**report, "reason": "Research configuration differs"}
    snapshots, accessed = [], set()
    for path, manifest in zip(paths, manifests, strict=True):
        with zipfile.ZipFile(path.with_name("source.zip")) as archive:
            hashes = json.loads(archive.read("snapshot-hashes.json"))
            if set(archive.namelist()) != {*hashes, "snapshot-hashes.json"}:
                return {**report, "reason": "Source archive contains unlisted or missing entries"}
            if fingerprint(hashes) != manifest["source_tree_sha256"] or any(
                hashlib.sha256(archive.read(name)).hexdigest() != expected for name, expected in hashes.items()
            ):
                return {**report, "reason": "Source snapshot integrity failed"}

            def read(name: str) -> str:
                accessed.add(name)
                return archive.read(name).decode("utf8")

            selected = pricing_templates(StudyConfig.model_validate(manifest["config"]), read)
            if any(selected[i] != manifest["pricing_templates"][label]
                   for i, label in enumerate(("system", "action"))):
                return {**report, "reason": "Active pricing template does not match its snapshot"}
            snapshots.append(hashes)
    changed = sorted(name for name in set().union(*snapshots)
                     if len({saved.get(name) for saved in snapshots}) > 1)
    report.update(changed_paths=changed, accessed_pricing_paths=sorted(accessed),
                  original_source_identities=sorted({m["source_tree_sha256"] for m in manifests}),
                  original_comparison_keys=sorted({m["comparison_key"] for m in manifests}))
    if not changed:
        return {**report, "reason": "No unused-template change explains the identity mismatch"}
    for name in changed:
        candidate = PurePosixPath(name)
        if (not name.startswith("prompts/pricing/") or candidate.suffix != ".txt"
                or ".." in candidate.parts or "\\" in name or name in accessed):
            return {**report, "reason": f"Change affects executable, protected or selected input: {name}"}
    return {**report, "accepted": True,
            "reason": "Only unselected pricing templates differ; active templates, code, protocol, configuration and economics match",
            "scope": "Explicit exploratory reanalysis; original manifests and source identities are preserved"}
