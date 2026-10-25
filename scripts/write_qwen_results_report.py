"""Write the detailed Qwen results report from verified exports, without running agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


def load(path: Path) -> Any:
    """Read the already exported analysis data."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path: Path) -> str:
    """Hash the saved bytes to detect changes to source or derived evidence."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: Any, digits: int = 6) -> str:
    """Format report displays while retaining unrounded values in the CSV files."""
    if value is None:
        return "Not applicable / unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return "≈0" if abs(value) < 1e-12 else f"{value:,.{digits}f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]], digits: int = 6) -> str:
    """Render a labeled Markdown table without changing the underlying measurements."""
    result = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    result += ["| " + " | ".join(number(v, digits) for v in row) + " |" for row in rows]
    return "\n".join(result)


def profile(row: dict[str, Any]) -> str:
    """Display the three actual firm prices in their fixed firm-ID order."""
    return "/".join(str(row[f"price_firm_{i}_ticks"]) for i in range(3))


def main() -> None:
    """Create a self-contained report folder and check all inputs again afterward."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    data, output = args.data.resolve(), args.output.resolve()
    if not data.is_relative_to(root) or not output.is_relative_to(root) or output.exists():
        raise ValueError("Read project-local exports and create a new report directory")
    provenance = load(data / "provenance.json")
    for relative, expected in provenance["outputs_sha256"].items():
        if digest(data / relative) != expected:
            raise ValueError(f"Changed exported evidence: {relative}")
    for relative, expected in provenance["source_sha256"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"Changed source evidence: {relative}")
    report = load(data / "report-data.json")
    studies = report["studies"]
    runs = [r for study in studies.values() for r in study["runs"]]
    output.mkdir(parents=True)
    (output / "data").mkdir()
    (output / "figures").mkdir()
    for path in data.glob("*.csv"):
        shutil.copyfile(path, output / "data" / path.name)
    for path in data.glob("*.png"):
        shutil.copyfile(path, output / "figures" / path.name)
    shutil.copyfile(data / "provenance.json", output / "data/analysis-provenance.json")
    configurations = {
        phase: {
            "sweep": study["sweep"]["sweep_id"],
            "config": study["sweep"]["base_config"],
            "source_tree_sha256": study["runs"][0]["manifest"]["source_tree_sha256"],
            "protocol_sha256": study["runs"][0]["manifest"]["protocol_sha256"],
        }
        for phase, study in studies.items()
    }
    (output / "data/configurations.json").write_text(
        json.dumps(configurations, indent=2), encoding="utf-8"
    )
    tokens: dict[str, str] = {}
    for phase, study in studies.items():
        summaries = [r["summary"] for r in study["runs"]]
        tokens[f"{phase.upper()}_OUTCOMES"] = table(
            [
                "Arm",
                "Whole mean price",
                "Tail mean price",
                "Whole mean profit",
                "Tail mean profit",
                "Whole elevation",
                "Tail elevation",
                "Tail price SD",
            ],
            [
                [
                    s["arm"],
                    s["whole_mean_price"],
                    s["mean_tail_price"],
                    s["whole_mean_gross_profit"],
                    s["mean_tail_profit"],
                    s["whole_profit_elevation"],
                    s["gross_profit_elevation"],
                    s["mean_tail_price_dispersion"],
                ]
                for s in summaries
            ],
        )
        tokens[f"{phase.upper()}_RESOURCES"] = table(
            [
                "Arm",
                "Requests",
                "First replies accepted",
                "Format corrections",
                "Length-limited replies",
                "Input tokens",
                "Output tokens",
                "Mean request sec",
                "Run elapsed sec",
            ],
            [
                [
                    s["arm"],
                    s["model_calls"],
                    s["valid_first_reply_decisions"],
                    s["format_correction_requests"],
                    s["length_limited_responses"],
                    s["input_tokens"],
                    s["output_tokens"],
                    s["mean_request_seconds"],
                    s["elapsed_seconds"],
                ]
                for s in summaries
            ],
            3,
        )
        tokens[f"{phase.upper()}_AUDITOR"] = table(
            [
                "Arm",
                "Completed rounds",
                "Profit above cutoff rounds",
                "Full windows",
                "Profit passes in every window round",
                "Spread passes in every window round",
                "Triggered windows",
                "Real warnings consumed",
                "Neutral notices consumed",
            ],
            [
                [
                    s["arm"],
                    s["rounds"],
                    s["profit_threshold_passing_rounds"],
                    s["full_windows"],
                    s["profit_passing_windows"],
                    s["spread_passing_windows"],
                    s["audit_trigger_rounds"],
                    s["consumed_warning_notices"],
                    s["consumed_neutral_notices"],
                ]
                for s in summaries
            ],
        )
        rows = []
        for run in study["runs"]:
            for row in run["rounds"]:
                if phase != "original":
                    rows.append(
                        [
                            row["arm"],
                            row["round"],
                            profile(row),
                            row["mean_price_ticks"],
                            row["price_spread_ticks"],
                            row["mean_gross_profit_currency"],
                            row["gross_profit_elevation"],
                            row["neutral_notices_consumed"],
                            row["model_requests"],
                        ]
                    )
        if rows:
            tokens[f"{phase.upper()}_ROUNDS"] = table(
                [
                    "Arm",
                    "Round",
                    "Prices F0/F1/F2",
                    "Mean price",
                    "Spread",
                    "Mean gross profit",
                    "Profit elevation",
                    "Neutral recipient notices consumed",
                    "Requests",
                ],
                rows,
            )
    resources = []
    for label, selected in (
        ("Original 400", runs[:4]),
        ("Natural 32", studies["natural"]["runs"]),
        ("Neutral 18", studies["neutral"]["runs"]),
        ("Combined 50 (accounting only)", runs[4:]),
    ):
        summaries = [r["summary"] for r in selected]
        calls = [c for r in selected for c in r["calls"]]
        ins, outs = sum(s["input_tokens"] for s in summaries), sum(
            s["output_tokens"] for s in summaries
        )
        resources.append(
            [
                label,
                sum(s["rounds"] for s in summaries),
                sum(s["decisions"] for s in summaries),
                len(calls),
                ins,
                outs,
                mean(c["elapsed_seconds"] for c in calls),
                sum(s["elapsed_seconds"] for s in summaries),
                (ins * 3 + outs * 15) / 1e6,
            ]
        )
    tokens["RESOURCE_TOTALS"] = table(
        [
            "Set",
            "Rounds",
            "Decisions",
            "Requests",
            "Input tokens",
            "Output tokens",
            "Mean request sec",
            "Summed run sec",
            "Illustrative USD at 3/M input +15/M output",
        ],
        resources,
        6,
    )
    timing = []
    for phase, study in studies.items():
        for run in sorted(study["runs"], key=lambda r: r["summary"]["execution_order"]):
            s = run["summary"]
            timing.append(
                [
                    phase,
                    s["execution_order"],
                    s["arm"],
                    s["started_at_cairo"],
                    s["ended_at_cairo"],
                    s["first_deviation_round"],
                    s["first_consumed_notice_round_or_none"],
                ]
            )
    tokens["TIMELINE"] = table(
        [
            "Design",
            "Execution position (0-based)",
            "Arm",
            "Start in Cairo",
            "End in Cairo",
            "First deviation",
            "First notice consumption",
        ],
        timing,
    )
    contrast_rows = []
    for phase, study in studies.items():
        for name, value in study["analysis"]["central_family"]["comparisons"].items():
            contrast_rows.append(
                [
                    phase,
                    name,
                    value["mean_difference"],
                    value["paired_runs"],
                    value["missing_complete_pairs"],
                    value["status"],
                    value["sign_flip_p"],
                    value["holm_p"],
                    value["simultaneous_95_bounded_interval"],
                ]
            )
    tokens["CONTRASTS"] = table(
        [
            "Design",
            "Contrast",
            "Tail score difference",
            "Complete pairs",
            "Missing assigned pairs",
            "Status",
            "Raw p",
            "Holm p",
            "Simultaneous bounded interval",
        ],
        contrast_rows,
    )
    identity_rows = []
    for phase, study in studies.items():
        first = study["runs"][0]["manifest"]
        identity_rows.append(
            [
                phase,
                study["sweep"]["sweep_id"],
                first["source_tree_sha256"],
                first["protocol_sha256"],
            ]
        )
    tokens["IDENTITIES"] = table(
        ["Design", "Sweep identity", "Archived source-tree SHA-256", "Frozen protocol SHA-256"],
        identity_rows,
    )
    segments, appendix = [], []
    for run in studies["original"]["runs"]:
        pieces: list[list[Any]] = []
        for row in run["rounds"]:
            current = profile(row)
            if pieces and pieces[-1][2] == current:
                pieces[-1][1] = row["round"]
            else:
                pieces.append([row["round"], row["round"], current])
        segments += [[run["summary"]["arm"], f"{a}–{b}" if a != b else a, p] for a, b, p in pieces]
        appendix.append(
            f"### A.{len(appendix)+1} Original {run['summary']['arm'].title()}: all 100 rounds\n\n"
            + table(
                [
                    "Round",
                    "Prices F0/F1/F2",
                    "Mean price",
                    "Spread",
                    "Mean gross profit",
                    "Profit elevation",
                    "Neutral notices consumed",
                    "Requests",
                    "Format corrections",
                ],
                [
                    [
                        r["round"],
                        profile(r),
                        r["mean_price_ticks"],
                        r["price_spread_ticks"],
                        r["mean_gross_profit_currency"],
                        r["gross_profit_elevation"],
                        r["neutral_notices_consumed"],
                        r["model_requests"],
                        r["format_correction_requests"],
                    ]
                    for r in run["rounds"]
                ],
            )
        )
    tokens["ORIGINAL_SEGMENTS"] = table(
        ["Arm", "Consecutive rounds", "Exact price profile F0/F1/F2"], segments
    )
    tokens["ORIGINAL_ALL_ROUNDS"] = "\n\n".join(appendix)
    recipient_groups = Counter(
        (n["study_component"], n["arm"], n["source_round"], n["effective_round"], n["kind"])
        for run in runs
        for n in run["notices"]
    )
    tokens["NOTICE_TIMING"] = table(
        [
            "Design",
            "Arm",
            "Source round",
            "First eligible decision",
            "Content",
            "Recipients consuming",
        ],
        [[*key, value] for key, value in recipient_groups.items()],
    )
    firms = []
    for run in runs:
        tail = run["rounds"][-run["summary"]["tail_rounds"] :]
        for i in range(3):
            firms.append(
                [
                    run["summary"]["study_component"],
                    run["summary"]["arm"],
                    i,
                    mean(r[f"price_firm_{i}_ticks"] for r in tail),
                    mean(r[f"gross_profit_firm_{i}_currency"] for r in tail),
                    sum(r[f"gross_profit_firm_{i}_currency"] for r in run["rounds"]),
                ]
            )
    tokens["FIRM_OUTCOMES"] = table(
        [
            "Design",
            "Arm",
            "Firm",
            "Tail mean price",
            "Tail mean own gross profit",
            "Cumulative own gross profit",
        ],
        firms,
    )
    source_archives = [
        root / studies[p]["runs"][0]["summary"]["manifest"] for p in ("original", "natural")
    ]
    snapshots = []
    for path in source_archives:
        with zipfile.ZipFile(path.with_name("source.zip")) as archive:
            snapshots.append(load_bytes(archive.read("snapshot-hashes.json")))
    differences = []
    for path in sorted(set(snapshots[0]) | set(snapshots[1])):
        if snapshots[0].get(path) == snapshots[1].get(path):
            continue
        differences.append(
            [
                path,
                (
                    "Added"
                    if path not in snapshots[0]
                    else "Removed" if path not in snapshots[1] else "Changed"
                ),
                snapshots[0].get(path),
                snapshots[1].get(path),
            ]
        )
    tokens["SOURCE_CHANGES"] = table(
        ["Archived path", "Change", "Original SHA-256", "Later SHA-256"], differences
    )
    examples = []
    for phase, arm, round_number, agent in (
        ("original", "private", 1, 0),
        ("original", "silent", 1, 0),
        ("natural", "silent", 1, 1),
        ("natural", "private", 1, 0),
        ("natural", "public", 2, 0),
        ("neutral", "public", 1, 0),
        ("neutral", "public", 4, 1),
        ("neutral", "private", 4, 1),
    ):
        run = next(r for r in studies[phase]["runs"] if r["summary"]["arm"] == arm)
        example = next(
            e for e in run["examples"] if e["round"] == round_number and e["agent_id"] == agent
        )
        text = example["response"][:600]
        if len(example["response"]) > 600:
            text += " [report excerpt ends; full response remains in events.jsonl]"
        examples.append(
            f"**{phase} / {arm} / round {round_number} / Firm {agent} / {example['call_id']}**\n\n"
            + "\n".join("> " + line for line in text.splitlines())
        )
    tokens["RESPONSE_EXAMPLES"] = "\n\n".join(examples)
    (output / "data/model-response-examples.json").write_text(
        json.dumps([e for run in runs for e in run["examples"]], indent=2), encoding="utf-8"
    )
    template_path = root / "scripts/templates/qwen-results-report.md"
    text = template_path.read_text(encoding="utf-8")
    for key, value in tokens.items():
        text = text.replace("{{" + key + "}}", value)
    unresolved = re.findall(r"\{\{[^}]+\}\}", text)
    if unresolved:
        raise ValueError(f"Unresolved report fields: {unresolved}")
    (output / "REPORT.md").write_text(text, encoding="utf-8")
    (output / "data/source-snapshot-changes.json").write_text(
        json.dumps(differences, indent=2), encoding="utf-8"
    )
    details = {"rounds": len(runs[0]["rounds"][0]), "runs": len(runs)}
    for relative, expected in provenance["source_sha256"].items():
        if digest(root / relative) != expected:
            raise ValueError("A raw input changed while writing the report")
    report_provenance = {
        "source_analysis": data.relative_to(root).as_posix(),
        "source_files_verified": len(provenance["source_sha256"]),
        "source_files_changed": 0,
        "experimental_rounds_rerun": 0,
        "model_requests_made": 0,
        "paid_calls": 0,
        "main_round_rows": 450,
        "main_decisions": 1350,
        "main_model_requests": 1528,
        "main_run_count": len(runs),
        "template_sha256": digest(template_path),
        "renderer_sha256": digest(Path(__file__)),
        "report_sha256": digest(output / "REPORT.md"),
        "data_sha256": {p.name: digest(p) for p in (output / "data").iterdir() if p.is_file()},
    }
    (output / "REPORT-PROVENANCE.json").write_text(
        json.dumps(report_provenance, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "report": (output / "REPORT.md").relative_to(root).as_posix(),
                "words": len(text.split()),
                "source_files_unchanged": len(provenance["source_sha256"]),
                "runs": details["runs"],
            },
            indent=2,
        )
    )


def load_bytes(value: bytes) -> Any:
    """Read the saved hash inventory from a frozen source archive."""
    return json.loads(value)


if __name__ == "__main__":
    main()
