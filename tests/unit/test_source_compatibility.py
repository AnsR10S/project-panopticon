"""The explicit recovery policy must not accept changed execution or selected prompts."""

import hashlib
import json
import zipfile

import pytest

from auditors_paradox.config import StudyConfig
from auditors_paradox.evaluation.source_compatibility import review_unused_prompt_drift
from auditors_paradox.evidence import fingerprint
from auditors_paradox.pricing import pricing_templates


def evidence(root, tmp_path, edits=None, *, change_config=False, confirmatory=False):
    cfg = StudyConfig.model_validate({"agent": {"prompt_profile": "price_only"}})
    sources = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "prompts").rglob("*.txt")}
    sources["src/engine.py"] = b"original execution\n"
    paths = []
    for index in range(2):
        content = dict(sources)
        if index:
            content.update(edits or {})
        hashes = {k: hashlib.sha256(v).hexdigest() for k, v in content.items()}
        directory = tmp_path / str(index)
        directory.mkdir()
        with zipfile.ZipFile(directory / "source.zip", "w") as archive:
            for name, data in content.items():
                archive.writestr(name, data)
            archive.writestr("snapshot-hashes.json", json.dumps(hashes))
        config = cfg.model_dump(mode="json")
        if index and change_config:
            config["memory_window"] = 5
        if confirmatory:
            config["run_kind"] = "confirmatory"
        system, action = pricing_templates(cfg, lambda name: content[name].decode())
        manifest = {"config": config, "pricing_templates": {"system": system, "action": action},
                    "feedback_texts": {"warning": "same warning"}, "protocol_sha256": "same",
                    "upstream_commit": "same", "benchmarks": {"nash_price": 137},
                    "data_class": "scripted_engineering_fixture", "enforcement": "disabled",
                    "comparison_key": str(index), "source_tree_sha256": fingerprint(hashes)}
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest))
        paths.append(path)
    return paths


def test_explicit_unused_pricing_file_change_is_explained(root, tmp_path):
    paths = evidence(root, tmp_path, {"prompts/pricing/explicit-economics.txt": b"unused replacement"})
    review = review_unused_prompt_drift(paths, root)
    assert review["accepted"]
    assert review["changed_paths"] == ["prompts/pricing/explicit-economics.txt"]


def test_added_unused_copy_is_permitted(root, tmp_path):
    paths = evidence(root, tmp_path, {"prompts/pricing/unused-copy.txt": b"not selected"})
    assert review_unused_prompt_drift(paths, root)["accepted"]


@pytest.mark.parametrize("name", ["src/engine.py", "docs/research/notes.md", "prompts/feedback/warning.txt"])
def test_execution_and_protected_input_drift_rejected(root, tmp_path, name):
    paths = evidence(root, tmp_path, {name: b"changed"})
    assert not review_unused_prompt_drift(paths, root)["accepted"]


def test_selected_prompt_rejected(root, tmp_path):
    raw = (root / "prompts/pricing/system.txt").read_bytes()
    paths = evidence(root, tmp_path, {"prompts/pricing/system.txt": raw + b"different instruction\n"})
    assert not review_unused_prompt_drift(paths, root)["accepted"]


def test_configuration_drift_rejected(root, tmp_path):
    paths = evidence(root, tmp_path, {"prompts/pricing/unused.txt": b"x"}, change_config=True)
    assert not review_unused_prompt_drift(paths, root)["accepted"]


def test_confirmatory_exception_disallowed(root, tmp_path):
    paths = evidence(root, tmp_path, {"prompts/pricing/unused.txt": b"x"}, confirmatory=True)
    assert not review_unused_prompt_drift(paths, root)["accepted"]


def test_unexplained_identity_change_rejected(root, tmp_path):
    assert not review_unused_prompt_drift(evidence(root, tmp_path), root)["accepted"]


def test_archive_checksum_failure_rejected(root, tmp_path):
    paths = evidence(root, tmp_path, {"prompts/pricing/unused.txt": b"x"})
    with zipfile.ZipFile(paths[-1].with_name("source.zip"), "a") as archive:
        archive.writestr("unlisted.txt", "extra unlisted source")
    assert not review_unused_prompt_drift(paths, root)["accepted"]
