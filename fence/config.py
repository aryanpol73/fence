"""fence.toml: every option, its default, and validation.

Zero config must work, so every field has a default. Unknown keys are errors rather
than silently ignored, because a typo in a policy file would otherwise weaken it
without anyone noticing.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CONFIG_FILE = "fence.toml"
BANDS = ("low", "medium", "high", "critical")
Band = Literal["low", "medium", "high", "critical"]
Grade = Literal["A", "B", "C", "D"]


class ConfigError(Exception):
    """fence.toml could not be read or did not validate."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class IndexConfig(_Section):
    refs: list[str] = ["HEAD"]
    history_depth: int | None = Field(None, ge=1, description="Max commits to index; None = all")
    since_date: str | None = Field(None, description="ISO date; older commits are skipped")
    include: list[str] = ["**"]
    exclude: list[str] = [
        "vendor/**",
        "third_party/**",
        "node_modules/**",
        "**/*.min.js",
        "**/*.lock",
        "**/package-lock.json",
    ]
    languages: list[str] = ["python", "javascript", "typescript", "go", "java"]
    ignore_revs_file: str = ".git-blame-ignore-revs"
    max_file_size: int = Field(1_000_000, ge=1_000)
    follow_renames_sensitivity: int = Field(50, ge=1, le=100, description="git -M percent")
    batch_size: int = Field(500, ge=1, le=10_000)
    bulk_commit_files: int = Field(100, ge=2, description="Commits touching more are 'bulk'")


class GithubConfig(_Section):
    enabled: Literal["auto", "off"] = "auto"
    token_env: str = "GITHUB_TOKEN"  # noqa: S105 - the env var's name, not a secret
    api_base_url: str = "https://api.github.com"
    rate_limit_policy: Literal["stop", "wait"] = "stop"
    min_remaining: int = Field(50, ge=0)
    sync_prs: bool = True
    sync_issues: bool = True
    sync_review_comments: bool = True
    max_age_days: int | None = Field(None, ge=1)
    max_requests_per_run: int = Field(2_000, ge=1)

    @field_validator("api_base_url")
    @classmethod
    def _https_only(cls, v: str) -> str:
        # A token goes to this host; plain http would leak it.
        if not v.startswith("https://"):
            raise ValueError("api_base_url must use https://")
        return v.rstrip("/")


class BandThresholds(_Section):
    medium: int = Field(25, ge=1, le=100)
    high: int = Field(60, ge=1, le=100)
    critical: int = Field(85, ge=1, le=100)


class ScoringConfig(_Section):
    weights: dict[str, float] = {}
    bands: BandThresholds = BandThresholds()
    min_confidence_to_warn: float = Field(0.3, ge=0.0, le=1.0)
    grade_floor_for_block: Grade = "C"

    def band(self, score: float) -> Band:
        if score >= self.bands.critical:
            return "critical"
        if score >= self.bands.high:
            return "high"
        if score >= self.bands.medium:
            return "medium"
        return "low"


class PathOverride(_Section):
    fail_on_band: Band | None = None
    require_ack_for: Band | None = None
    risk_bonus: int = Field(0, ge=0, le=50)


class PolicyConfig(_Section):
    fail_on_band: Band = "high"
    require_ack_for: Band = "critical"
    ignore_paths: list[str] = []
    path_overrides: dict[str, PathOverride] = {
        "**/auth/**": PathOverride(fail_on_band="medium", risk_bonus=10),
        "**/payments/**": PathOverride(fail_on_band="medium", risk_bonus=10),
        "**/migrations/**": PathOverride(fail_on_band="medium", risk_bonus=10),
    }
    allow_bypass_env: str = "FENCE_BYPASS"
    ack_expiry_days: int | None = Field(90, ge=1)
    ack_ledger: str = ".fence/acks.jsonl"


class LlmConfig(_Section):
    enabled: bool = False
    provider: str = "anthropic"
    model: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens_per_run: int = Field(50_000, ge=0)
    max_usd_per_run: float = Field(0.50, ge=0.0)
    cache_dir: str | None = None
    redact_paths: list[str] = ["secrets/**", "**/*.env", "**/.env"]


class OutputConfig(_Section):
    format: Literal["human", "json"] = "human"
    color: Literal["auto", "always", "never"] = "auto"
    verbosity: int = Field(0, ge=0, le=2)
    max_items: int = Field(10, ge=1, le=500)
    link_style: Literal["url", "sha"] = "url"


class McpConfig(_Section):
    max_response_tokens: int = Field(1_500, ge=200, le=20_000)
    detail_default: Literal["brief", "full"] = "brief"
    write_tools_enabled: bool = False
    guardrail_band: Band = "high"


class PerformanceConfig(_Section):
    worker_count: int = Field(4, ge=1, le=64)
    memory_ceiling_mb: int = Field(512, ge=64)
    # Outside the working tree by default: the index holds private repo content.
    cache_dir: str = "~/.cache/fence"
    git_timeout_seconds: float = Field(10.0, gt=0, le=600)
    log_timeout_seconds: float = Field(600.0, gt=0, le=86_400)
    hook_budget_seconds: float = Field(2.0, gt=0, le=60)


class Config(_Section):
    index: IndexConfig = IndexConfig()
    github: GithubConfig = GithubConfig()
    scoring: ScoringConfig = ScoringConfig()
    policy: PolicyConfig = PolicyConfig()
    llm: LlmConfig = LlmConfig()
    output: OutputConfig = OutputConfig()
    mcp: McpConfig = McpConfig()
    performance: PerformanceConfig = PerformanceConfig()
    source: str | None = Field(None, description="Where this config came from")

    @property
    def cache_dir(self) -> Path:
        override = os.environ.get("FENCE_CACHE_DIR")
        return Path(override or self.performance.cache_dir).expanduser()


def load_config(repo_root: Path | None = None, *, text: str | None = None) -> Config:
    """Read fence.toml from `repo_root` (or parse `text`). No file -> all defaults."""
    source = "<defaults>"
    if text is None and repo_root is not None:
        path = Path(repo_root) / CONFIG_FILE
        if path.is_file():
            if path.stat().st_size > 256_000:
                raise ConfigError(f"{path} is larger than 256 KB")
            text = path.read_text(encoding="utf-8")
            source = str(path)
    elif text is not None:
        source = "<text>"
    return parse_config(text or "", source=source)


def parse_config(text: str, *, source: str = "<text>") -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{source}: invalid TOML: {e}") from None
    try:
        return Config.model_validate({**data, "source": source})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"{source}: {problems}") from None


def bands_at_or_above(band: Band) -> tuple[Band, ...]:
    return BANDS[BANDS.index(band) :]  # type: ignore[return-value]
