"""Prepare a tiny gateway check and a four-arm pilot using explicit account prices."""

from __future__ import annotations

import argparse
from pathlib import Path

from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.runner import preflight


def prepare(
    root: Path,
    model: str,
    input_rate: float,
    output_rate: float,
    smoke_budget: float,
    full_budget: float,
    base_url: str,
    token_parameter: str,
    destination: Path | None = None,
    client: str = "sdk",
) -> list[Path]:
    """Write only validated, secret-free local configs without sending any requests."""
    base = load_config(root / "configs/diagnostics/local-repair-natural.json")
    target = (destination or root / "configs/local").resolve()
    if not target.is_relative_to((root / "configs/local").resolve()):
        raise ValueError("Provider configs must stay inside configs/local")
    configs = []
    for mode in ("smoke", "full"):
        data = base.model_dump(mode="json")
        small = mode == "smoke"
        data.update(
            study_id=f"agentrouter-{'qwen-' if client == 'qwen_code' else ''}{mode}",
            run_kind="pilot",
            study_purpose="diagnostic" if small else "research",
            arm="silent",
            rounds=3 if small else 100,
            analysis_last_rounds=3 if small else 20,
            memory_window=20,
            batch_arms=["silent"] if small else ["silent", "private", "public", "placebo"],
        )
        data["protocol"] = "docs/research/agentrouter-pilot-2026-10-22.md"
        data["agent"].update(
            backend="agentrouter",
            client=client,
            model=model,
            base_url=base_url,
            api_protocol="responses" if client == "qwen_code" else "chat",
            temperature=None,
            max_output_tokens=8192 if client == "qwen_code" else 768,
            chat_token_parameter=token_parameter,
            timeout_seconds=300 if client == "qwen_code" else 90,
            transport_attempts=3 if client == "qwen_code" else 2,
            min_request_interval_seconds=20 if client == "qwen_code" else 0.3,
            reasoning_effort="low" if client == "qwen_code" else None,
            retry_max_seconds=60 if client == "qwen_code" else 30,
            local_thinking=None,
            local_num_gpu=None,
        )
        data["feedback"].update(
            warning_source="live", schedule_rounds=[] if small else [10, 30, 50, 70, 90]
        )
        data["budget"].update(
            max_calls=18 if small else 900,
            max_elapsed_seconds=600 if small else 3600,
            max_cost_usd=smoke_budget if small else full_budget,
            input_usd_per_million=input_rate,
            output_usd_per_million=output_rate,
        )
        cfg = StudyConfig.model_validate(data)
        preflight(cfg, root, allow_paid=True)
        configs.append(
            (target / f"agentrouter-{'qwen-' if client == 'qwen_code' else ''}{mode}.json", cfg)
        )
    if any(path.exists() for path, _ in configs):
        raise ValueError(
            "AgentRouter configs already exist; edit those files instead of overwriting them"
        )
    target.mkdir(parents=True, exist_ok=True)
    for path, cfg in configs:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(cfg.model_dump_json(indent=2) + "\n")
    return [path for path, _ in configs]


def main() -> None:
    """Require real account rates and keep the key entirely outside configuration files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--client", choices=["sdk", "qwen_code"], default="sdk")
    parser.add_argument("--input-rate", type=float, required=True)
    parser.add_argument("--output-rate", type=float, required=True)
    parser.add_argument("--smoke-budget", type=float, default=1)
    parser.add_argument("--full-budget", type=float, default=20)
    parser.add_argument("--base-url", default="https://agentrouter.org/v1")
    parser.add_argument(
        "--token-parameter",
        choices=["max_tokens", "max_completion_tokens"],
        default="max_completion_tokens",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for path in prepare(
        root,
        args.model,
        args.input_rate,
        args.output_rate,
        args.smoke_budget,
        args.full_budget,
        args.base_url,
        args.token_parameter,
        client=args.client,
    ):
        print(path.relative_to(root).as_posix())
    print("Prepared configs only; no model requests. Your API token is not stored here.")


if __name__ == "__main__":
    main()
