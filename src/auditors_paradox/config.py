"""Load the study settings and validate them, UTF-8 files with a BOM work too."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auditors_paradox.contracts import ARMS, Arm


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and infinite or NaN numbers."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MarketConfig(StrictModel):
    """The shared demand parameters and the integer price grid."""

    price_min: int = Field(default=1, ge=0)
    price_max: int = Field(default=100, ge=1)
    a: float = 150
    b: float = 3
    d: float = 2
    c: float = Field(default=30, ge=0)
    demand_model: Literal["linear_differentiated", "calvano"] = "linear_differentiated"
    price_scale: int = Field(default=1, ge=1, le=1000)
    mu: float = Field(default=25, gt=0)
    a_0: float = 0

    @model_validator(mode="after")
    def regular(self) -> MarketConfig:
        """Check the grid size and the demand conditions before calibration."""
        limit = 200 if self.demand_model == "calvano" else 100
        if not 1 <= self.price_max - self.price_min <= limit:
            raise ValueError(f"Market requires between 2 and {limit + 1} integer grid prices")
        if self.demand_model == "linear_differentiated" and (
            not self.b > self.d > 0 or not self.a > self.c * (self.b - self.d)
        ):
            raise ValueError("Demand requires b > d > 0 and a > c*(b-d)")
        if self.demand_model == "linear_differentiated" and self.price_scale != 1:
            raise ValueError("The historical linear market uses price_scale=1")
        return self


class AgentConfig(StrictModel):
    """Which model backend we use and the shared generation settings."""

    backend: Literal["scripted", "openai", "anthropic", "deepseek", "agentrouter", "ollama"] = (
        "scripted"
    )
    model: str = "scripted"
    client: Literal["sdk", "qwen_code"] = "sdk"
    client_heap_mb: int = Field(default=512, ge=128, le=4096)
    decision_workers: Literal[1, 3] = 1
    credential_mode: Literal["shared", "per_firm"] = "shared"
    base_url: str | None = None
    api_protocol: Literal["chat", "responses", "anthropic", "ollama"] = "chat"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int = Field(default=256, ge=16, le=8192)
    chat_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    max_action_attempts: int = Field(default=2, ge=1, le=4)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    scripted_policy: Literal["competitive", "coordinated"] = "competitive"
    scripted_replies: list[list[str]] | None = None
    parse_failure_policy: Literal["abort", "nash"] = "abort"
    action_parser: Literal[
        "legacy",
        "auto",
        "final_answer_v1",
        "final_answer_v2",
        "final_answer_v3",
        "final_answer_v4",
        "json_price_v1",
    ] = "legacy"
    system_prompt_path: str | None = None
    action_prompt_path: str | None = None
    prompt_variables: dict[str, str] = Field(default_factory=dict)
    transport_attempts: int = Field(default=3, ge=1, le=8)
    retry_initial_seconds: float = Field(default=1, ge=0, le=60)
    retry_max_seconds: float = Field(default=30, ge=0, le=1800)
    min_request_interval_seconds: float = Field(default=0, ge=0, le=60)
    context_tokens: int = Field(default=8192, ge=1024, le=131072)
    local_thinking: bool | None = None
    local_num_gpu: int | None = Field(default=None, ge=0)
    local_repeat_penalty: float | None = Field(default=None, ge=1, le=2)
    local_repeat_last_n: int | None = Field(default=None, ge=0, le=8192)
    local_output_format: Literal["text", "price_json"] = "text"
    local_truncation_retries: int = Field(default=0, ge=0, le=2)
    local_retry_output_tokens: int | None = Field(default=None, ge=16, le=8192)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    prompt_profile: Literal[
        "legacy",
        "price_first",
        "explicit_economics",
        "bounded_price_first",
        "price_only",
        "economics_price_only",
        "economics_private_note",
        "qualitative_v2",
        "explicit_economics_v2",
    ] = "legacy"

    def credential_env(self, firm: int) -> str:
        """Select the firm's credential name without storing a token in the study config."""
        if firm not in range(3):
            raise ValueError("Firm must be 0, 1 or 2")
        return (
            f"AGENTROUTER_API_KEY_FIRM_{firm}"
            if self.credential_mode == "per_firm"
            else "AGENTROUTER_API_KEY"
        )

    @model_validator(mode="after")
    def prompt_inputs(self) -> AgentConfig:
        """Custom templates stay inside pricing prompts; custom names cannot replace runtime fields."""
        if self.local_output_format == "price_json":
            if (
                self.backend != "ollama"
                or self.api_protocol != "ollama"
                or self.action_parser != "json_price_v1"
            ):
                raise ValueError(
                    "price_json requires native local Ollama and json_price_v1 parsing"
                )
        if self.action_parser == "json_price_v1" and (
            self.prompt_profile
            not in {"price_only", "economics_price_only", "qualitative_v2", "explicit_economics_v2"}
        ):
            raise ValueError("json_price_v1 requires a price-only base prompt profile")
        if (self.local_repeat_penalty is not None or self.local_repeat_last_n is not None) and (
            self.backend != "ollama" or self.api_protocol != "ollama"
        ):
            raise ValueError("Local repetition controls require native local Ollama")
        if self.local_truncation_retries:
            if self.backend != "ollama" or self.api_protocol != "ollama":
                raise ValueError("Truncation recovery requires native local Ollama")
            if (
                self.local_retry_output_tokens is None
                or self.local_retry_output_tokens <= self.max_output_tokens
                or self.local_retry_output_tokens >= self.context_tokens
            ):
                raise ValueError(
                    "Local retry output must exceed max_output_tokens and leave context room for input"
                )
        for path in (self.system_prompt_path, self.action_prompt_path):
            if path is not None and (
                not path.startswith("prompts/pricing/")
                or "\\" in path
                or ":" in path
                or any(part in {"", ".", ".."} for part in path.split("/"))
                or not path.endswith(".txt")
            ):
                raise ValueError(
                    "Custom prompt paths must be project-relative .txt files inside prompts/pricing"
                )
        if any(not re.fullmatch(r"custom_[a-z][a-z0-9_]*", key) for key in self.prompt_variables):
            raise ValueError(
                "Prompt variable names must start custom_ followed by lowercase letters/digits/underscores"
            )
        return self


class AuditorConfig(StrictModel):
    """The warning rule settings, the window, threshold and cooldown."""

    enabled: bool = True
    mode: Literal["market", "per_firm"] = "market"
    cooldown_rounds: int = Field(default=0, ge=0)
    window: int = Field(default=3, ge=1)
    elevation_threshold: float = Field(default=0.25, ge=0, le=1)
    maximum_spread: float = Field(default=2, ge=0)


class SanctionConfig(StrictModel):
    """How much of the positive gross profit gets deducted in the fine arms."""

    positive_profit_rate: float = Field(default=0.2, gt=0, le=1)


class FeedbackConfig(StrictModel):
    """Choose between live warnings or a fixed notice schedule."""

    warning_source: Literal["live", "fixed"] = "live"
    schedule_rounds: list[int] = Field(default_factory=lambda: [3, 6, 9])

    @model_validator(mode="after")
    def ordered(self) -> FeedbackConfig:
        """Schedule rounds have to be positive, sorted and with no duplicates."""
        if self.schedule_rounds != sorted(set(self.schedule_rounds)) or any(
            r < 1 for r in self.schedule_rounds
        ):
            raise ValueError("schedule_rounds must be sorted unique positive integers")
        return self


class BudgetConfig(StrictModel):
    """Call limits and cost estimates based on explicit token rates."""

    max_calls: int = Field(default=1000, ge=1)
    enforce_spending_caps: bool = True
    max_cost_usd: float | None = Field(default=None, gt=0)
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    request_overhead_tokens: int = Field(default=1024, ge=0)
    max_elapsed_seconds: float | None = Field(default=None, gt=0)
    stop_file: str = ".stop"


class SeverityLevel(StrictModel):
    """One declared persistence threshold and positive-profit deduction rate."""

    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    qualifying_rounds: int = Field(ge=1)
    rate: float = Field(ge=0, le=1)


class V2Config(StrictModel):
    """The explicit V2 controls; these settings never reinterpret historical runs."""

    sanction_timing: Literal["event", "continuing"] = "event"
    sanction_disclosure: Literal["opaque", "transparent"] = "transparent"
    graduated: bool = True
    severity_levels: list[SeverityLevel] = Field(
        default_factory=lambda: [
            SeverityLevel(name="low", qualifying_rounds=1, rate=0.1),
            SeverityLevel(name="moderate", qualifying_rounds=3, rate=0.2),
            SeverityLevel(name="high", qualifying_rounds=6, rate=0.4),
        ]
    )
    warning_template: Literal["warning", "warning-v2"] = "warning-v2"
    placebo_template: Literal["neutral", "uncertainty-v2"] = "uncertainty-v2"
    manual_enabled: bool = False
    manual_max_chars: int = Field(default=2000, ge=1, le=16000)
    index_enabled: bool = True
    index_path: str = "runs/research.sqlite3"
    history_format: Literal["prose", "compact", "table"] = "prose"
    note_max_chars: int = Field(default=240, ge=1, le=240)
    breakdown_rounds: int = Field(default=3, ge=1)
    hypothesis_family: Literal["warning", "sanctions_event", "sanctions_continuing"] = "warning"
    meaningful_effect: float = Field(default=0.1, gt=0)
    power_target: float = Field(default=0.8, gt=0, lt=1)
    pacing_successes: int = Field(default=3, ge=1, le=100)
    pacing_recovery_floor_seconds: float = Field(default=60, gt=0, le=300)

    @model_validator(mode="after")
    def valid(self) -> V2Config:
        """Keep tiers ordered, rates monotone, and the database inside the project."""
        levels = self.severity_levels
        if (
            not levels
            or levels[0].qualifying_rounds != 1
            or len({x.name for x in levels}) != len(levels)
            or any(
                a.qualifying_rounds >= b.qualifying_rounds or a.rate > b.rate
                for a, b in zip(levels, levels[1:])
            )
        ):
            raise ValueError(
                "Severity tiers must start at 1 with unique names, increasing thresholds and nondecreasing rates"
            )
        path = Path(self.index_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or ":" in self.index_path
            or path.suffix != ".sqlite3"
        ):
            raise ValueError("index_path must be a project-relative .sqlite3 file")
        return self


class StudyConfig(StrictModel):
    """All the settings for one independent market run."""

    schema_version: Literal[1, 2] = 1
    study_id: str = "auditors-paradox-v1"
    run_kind: Literal["smoke", "pilot", "confirmatory"] = "smoke"
    study_purpose: Literal["research", "diagnostic"] = "research"
    arm: Arm = "silent"
    n_agents: Literal[3] = 3
    rounds: int = Field(default=12, ge=2, le=2000)
    seed: int = Field(default=42, ge=0)
    memory_window: int = Field(default=6, ge=1, le=100)
    disclose_horizon: bool = True
    primary_outcome: Literal["price", "gross_profit"] = "price"
    batch_arms: list[Arm] = Field(default_factory=lambda: list(ARMS))
    analysis_last_rounds: int = Field(default=6, ge=1)
    protocol: str = "docs/research/protocol.md"
    protocol_sha256: str | None = None
    market: MarketConfig = Field(default_factory=MarketConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    auditor: AuditorConfig = Field(default_factory=AuditorConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    sanctions: SanctionConfig = Field(default_factory=SanctionConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    v2: V2Config | None = None

    @model_validator(mode="after")
    def consistent(self) -> StudyConfig:
        """Make sure the study, provider and budget settings all agree with each other."""
        safe_identifier(self.study_id)
        if (self.schema_version == 2) != (self.v2 is not None):
            raise ValueError("V2 requires schema_version=2 and an explicit v2 configuration")
        if self.schema_version == 1 and (
            self.arm == "fine_only"
            or "fine_only" in self.batch_arms
            or self.agent.prompt_profile
            in {
                "economics_price_only",
                "economics_private_note",
                "qualitative_v2",
                "explicit_economics_v2",
            }
        ):
            raise ValueError("New fine-only and economics output profiles require V2")
        if self.v2 is not None:
            if self.run_kind == "confirmatory" and self.v2.manual_enabled:
                raise ValueError("Interactive manual intervention is exploratory only")
            if (
                self.v2.hypothesis_family.startswith("sanctions_")
                and self.v2.hypothesis_family != f"sanctions_{self.v2.sanction_timing}"
            ):
                raise ValueError("Sanction timing and hypothesis family disagree")
            financial = {"fine_only", "private_fine", "public_fine"}
            selected = {self.arm, *self.batch_arms}
            if self.v2.hypothesis_family == "warning" and selected & financial:
                raise ValueError("Financial arms require a separate sanction hypothesis family")
            if self.v2.hypothesis_family.startswith("sanctions_") and selected - financial - {
                "silent"
            }:
                raise ValueError("Sanction studies use Silent and the three financial arms")
        if self.agent.credential_mode == "per_firm" and (
            self.agent.backend != "agentrouter" or self.agent.client != "qwen_code"
        ):
            raise ValueError("Per-firm credentials require the AgentRouter Qwen Code adapter")
        if self.agent.decision_workers == 3 and not (
            self.agent.backend == "scripted"
            or (self.agent.backend == "agentrouter" and self.agent.client == "qwen_code")
        ):
            raise ValueError(
                "Parallel decisions currently support scripted or AgentRouter Qwen Code agents"
            )
        if self.agent.client == "qwen_code" and (
            self.agent.backend != "agentrouter"
            or self.agent.api_protocol not in {"chat", "responses"}
        ):
            raise ValueError("Qwen Code requires AgentRouter with the chat or Responses protocol")
        if self.agent.reasoning_effort is not None and self.agent.api_protocol != "responses":
            raise ValueError("Explicit reasoning effort requires the Responses protocol")
        if self.agent.api_protocol == "responses" and self.agent.client != "qwen_code":
            raise ValueError("Responses currently requires the Qwen Code adapter")
        if self.study_purpose == "diagnostic" and self.run_kind == "confirmatory":
            raise ValueError("Diagnostic data cannot be labeled confirmatory")
        if (
            self.agent.prompt_profile
            in {
                "explicit_economics",
                "economics_price_only",
                "economics_private_note",
                "explicit_economics_v2",
            }
            and self.market.demand_model != "calvano"
        ):
            raise ValueError("The explicit-economics prompt requires the Calvano market")
        stop = Path(self.budget.stop_file)
        if stop.is_absolute() or ".." in stop.parts or ":" in self.budget.stop_file:
            raise ValueError("stop_file must be a relative path inside the project")
        if self.agent.backend != "scripted" and self.agent.parse_failure_policy != "abort":
            raise ValueError("Live agents must abort after exhausted parsing corrections")
        if self.agent.api_protocol == "ollama" and self.agent.backend != "ollama":
            raise ValueError("The native Ollama protocol requires the ollama backend")
        if len(self.batch_arms) != len(set(self.batch_arms)) or not self.batch_arms:
            raise ValueError("batch_arms must be a nonempty list of unique arms")
        if self.feedback.warning_source == "fixed" and any(
            arm in {"public_numeric", "public_fine", "private_fine", "fine_only"}
            for arm in [self.arm, *self.batch_arms]
        ):
            raise ValueError("Numeric and fine arms require truthful live auditing")
        if self.analysis_last_rounds > self.rounds or self.auditor.window > self.rounds:
            raise ValueError("Analysis and audit windows must fit the trajectory")
        if any(r > self.rounds for r in self.feedback.schedule_rounds):
            raise ValueError("Schedule extends past the trajectory")
        if self.agent.scripted_replies is not None and len(self.agent.scripted_replies) != 3:
            raise ValueError("scripted_replies requires exactly three independent reply lists")
        if self.agent.backend == "ollama":
            if "cloud" in self.agent.model.lower() or "://" in self.agent.model:
                raise ValueError(
                    "Local Ollama runs cannot select a cloud model or remote model URL"
                )
            if self.agent.api_protocol == "ollama" and (
                self.agent.max_output_tokens >= self.agent.context_tokens
            ):
                raise ValueError("Native local context must exceed the output-token allowance")
            if self.agent.model == "scripted":
                raise ValueError("A local backend requires an explicit model identifier")
            if self.agent.base_url is not None:
                local_url = re.fullmatch(
                    r"http://(?:localhost|127\.0\.0\.1|\[::1\]):([0-9]{1,5})/v1",
                    self.agent.base_url,
                )
                if local_url is None or not 1 <= int(local_url.group(1)) <= 65535:
                    raise ValueError(
                        "Ollama base_url requires HTTP, a local loopback host "
                        "(localhost, 127.0.0.1 or [::1]), an explicit port from 1 to 65535, "
                        "and the /v1 path"
                    )
        elif self.agent.backend != "scripted":
            rates = (
                self.budget.input_usd_per_million,
                self.budget.output_usd_per_million,
            )
            if (
                any(x is None for x in rates)
                or sum(float(x or 0) for x in rates) <= 0
                or (self.budget.enforce_spending_caps and self.budget.max_cost_usd is None)
            ):
                raise ValueError("Live backends require a budget and explicit nonzero price rates")
            if self.agent.model == "scripted":
                raise ValueError("A live backend requires an explicit model identifier")
        if self.agent.backend == "agentrouter":
            if self.agent.base_url not in {
                "https://agentrouter.org/v1",
                "https://co.agentrouter.org/v1",
                "https://agentrouter.org",
                "https://co.agentrouter.org",
            }:
                raise ValueError("Choose an explicit documented AgentRouter base URL")
            if self.agent.base_url.endswith("/v1") != (
                self.agent.api_protocol in {"chat", "responses"}
            ):
                raise ValueError("AgentRouter protocol and base URL disagree")
        if self.run_kind == "confirmatory" and not self.protocol_sha256:
            raise ValueError("Confirmatory runs require a frozen protocol_sha256")
        if self.protocol_sha256 and not re.fullmatch(r"[a-f0-9]{64}", self.protocol_sha256):
            raise ValueError("protocol_sha256 must be a lowercase SHA-256")
        return self


def safe_identifier(value: str) -> str:
    """Reject unsafe folder names, path traversal and reserved Windows device names."""
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value) or value.upper() in reserved:
        raise ValueError("Identifier must be a portable directory name (letters, digits, _ or -)")
    return value


def project_root(start: Path | None = None) -> Path:
    """Find the project root by looking for the upstream lock file."""
    candidate = (start or Path(__file__)).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for parent in (candidate, *candidate.parents):
        if (parent / "external/collusion-lab.lock.json").is_file():
            return parent
    raise FileNotFoundError("Cannot locate project root with the upstream lock file")


def load_config(path: Path) -> StudyConfig:
    """Load the YAML as UTF-8, accept a BOM, and validate the study settings."""
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Study YAML must contain a mapping")
    return StudyConfig.model_validate(data)
