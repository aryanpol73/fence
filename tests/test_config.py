from pathlib import Path

import pytest

from fence.config import ConfigError, bands_at_or_above, load_config, parse_config


def test_zero_config_gives_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.source == "<defaults>"
    assert cfg.policy.fail_on_band == "high"
    assert cfg.llm.enabled is False
    assert cfg.scoring.grade_floor_for_block == "C"


def test_repo_file_is_read(tmp_path: Path) -> None:
    (tmp_path / "fence.toml").write_text('[policy]\nfail_on_band = "critical"\n')
    cfg = load_config(tmp_path)
    assert cfg.policy.fail_on_band == "critical"
    assert cfg.source.endswith("fence.toml")


def test_unknown_key_is_an_error_not_ignored() -> None:
    with pytest.raises(ConfigError, match="policy.fail_on_bnad"):
        parse_config('[policy]\nfail_on_bnad = "low"\n')


def test_bad_value_names_the_field() -> None:
    with pytest.raises(ConfigError, match="output.max_items"):
        parse_config("[output]\nmax_items = 0\n")


def test_invalid_toml() -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        parse_config("[policy\n")


def test_github_base_url_must_be_https() -> None:
    with pytest.raises(ConfigError, match="https"):
        parse_config('[github]\napi_base_url = "http://example.com"\n')


@pytest.mark.parametrize(
    ("score", "band"),
    [(0, "low"), (24.9, "low"), (25, "medium"), (59, "medium"), (60, "high"), (84, "high"),
     (85, "critical"), (100, "critical")],
)
def test_band_boundaries_match_brief(score: float, band: str) -> None:
    assert parse_config("").scoring.band(score) == band


def test_bands_at_or_above() -> None:
    assert bands_at_or_above("high") == ("high", "critical")


def test_cache_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path))
    assert parse_config("").cache_dir == tmp_path


def test_repo_fence_toml_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    assert load_config(root).llm.enabled is False
