\"\"\"Early monolithic runner prototype for executing scripted pilot rounds.\"\"\"

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from auditors_paradox.config import load_config
from auditors_paradox.evidence import EvidenceWriter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("prototype_runner")


def execute_mock_round(round_num: int, arm: str) -> dict[str, Any]:
    return {
        "round": round_num,
        "arm": arm,
        "prices": {"firm_a": 10.5, "firm_b": 10.5},
        "profits": {"firm_a": 42.0, "firm_b": 42.0},
        "audit_triggered": False,
        "feedback_sent": None,
    }


def run_prototype(config_path: Path, output_dir: Path) -> None:
    logger.info("Loading prototype config from %s", config_path)
    cfg = load_config(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    writer = EvidenceWriter(output_dir)

    logger.info("Executing study %s (%d rounds)", cfg.study_id, cfg.rounds)
    for r in range(cfg.rounds):
        data = execute_mock_round(r, cfg.arm)
        writer.record_round(r, data)
        if (r + 1) % 5 == 0:
            logger.info("Progress: completed round %d/%d", r + 1, cfg.rounds)

    writer.close()
    summary = {
        "study_id": cfg.study_id,
        "rounds_completed": cfg.rounds,
        "status": "prototype_complete",
    }
    summary_path = output_dir / "prototype_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("Prototype summary written to %s", summary_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prototype pilot runner")
    parser.add_argument("--config", type=Path, required=True, help="Path to YAML config")
    parser.add_argument(
        "--output", type=Path, default=Path("runs/prototype"), help="Output directory"
    )
    args = parser.parse_args()
    run_prototype(args.config, args.output)
