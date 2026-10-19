"""Here we make sure a public checkout works without the private research archive."""

import json

from auditors_paradox import cli


def test_public_checkout_reports_unavailable_archive(tmp_path, monkeypatch, capsys):
    """Doctor still passes without the archive, source verification just says unavailable."""
    monkeypatch.setattr(cli, "verify_upstream", lambda root: "pinned-test-commit")
    args = ["--project-root", str(tmp_path)]
    assert cli.main([*args, "doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["sources"]["status"] == "not_available"
    assert doctor["sources"]["valid"] is None
    assert doctor["sources"]["checked_files"] == 0
    assert cli.main([*args, "verify-sources"]) == 2


def test_partial_archive_is_not_treated_as_public_checkout(tmp_path, monkeypatch, capsys):
    """A half present archive gets rejected, we don't just skip checking it."""
    manifest = tmp_path / "docs/source-materials/source-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "verify_upstream", lambda root: "pinned-test-commit")
    assert cli.main(["--project-root", str(tmp_path), "doctor"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["sources"]["status"] == "incomplete_archive"
    assert result["sources"]["valid"] is False
