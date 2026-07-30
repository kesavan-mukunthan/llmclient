from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from llmclient.config import Config, load_config
from llmclient.errors import ConfigError

MINIMAL_CONFIG = """
[defaults]
timeout_seconds = 30.0
max_output_tokens = 1024

[models.sonnet]
vendor = "anthropic"
model_id = "claude-sonnet-4-6"
input_per_mtok = 3.00
output_per_mtok = 15.00
"""


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_explicit_config_path_is_used(tmp_path: Path) -> None:
    config_file = _write(tmp_path / "somewhere.toml", MINIMAL_CONFIG)

    config = load_config(config_path=config_file)

    assert config.source_path == config_file


def test_explicit_config_path_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(config_path=tmp_path / "does-not-exist.toml")


def test_explicit_config_path_skips_discovery_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Env var points at a nonexistent file, and a valid project file exists in
    # cwd — an explicit config_path must win over both, without even
    # consulting them.
    monkeypatch.setenv("LLMCLIENT_CONFIG", str(tmp_path / "nonexistent.toml"))
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "llmclient.toml", MINIMAL_CONFIG)

    explicit = _write(tmp_path / "explicit.toml", MINIMAL_CONFIG)
    config = load_config(config_path=explicit)

    assert config.source_path == explicit


def test_env_var_set_and_valid_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_config = _write(tmp_path / "env.toml", MINIMAL_CONFIG)
    monkeypatch.setenv("LLMCLIENT_CONFIG", str(env_config))

    config = load_config()

    assert config.source_path == env_config


def test_env_var_set_but_nonexistent_raises_without_fallthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLMCLIENT_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "llmclient.toml", MINIMAL_CONFIG)

    with pytest.raises(ConfigError):
        load_config()


def test_project_config_used_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLMCLIENT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    project_config = _write(tmp_path / "llmclient.toml", MINIMAL_CONFIG)

    config = load_config()

    assert config.source_path == project_config


def test_user_config_used_when_env_and_project_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLMCLIENT_CONFIG", raising=False)
    empty_cwd = tmp_path / "cwd"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)
    monkeypatch.setenv("HOME", str(tmp_path))

    user_config_dir = tmp_path / ".config" / "llmclient"
    user_config_dir.mkdir(parents=True)
    user_config = _write(user_config_dir / "config.toml", MINIMAL_CONFIG)

    config = load_config()

    assert config.source_path == user_config


def test_no_config_found_lists_all_three_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLMCLIENT_CONFIG", raising=False)
    empty_cwd = tmp_path / "cwd"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    message = str(exc_info.value)
    assert "LLMCLIENT_CONFIG" in message
    assert "llmclient.toml" in message
    assert str(tmp_path / ".config" / "llmclient" / "config.toml") in message


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_vendor_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace('vendor = "anthropic"', 'vendor = "openai"')
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="sonnet"):
        load_config(config_path=config_file)


@pytest.mark.parametrize(
    "field",
    ["vendor", "model_id", "input_per_mtok", "output_per_mtok"],
)
def test_missing_required_model_field_raises(tmp_path: Path, field: str) -> None:
    lines = [
        line
        for line in MINIMAL_CONFIG.splitlines()
        if not line.strip().startswith(field)
    ]
    config_file = _write(tmp_path / "llmclient.toml", "\n".join(lines))

    with pytest.raises(ConfigError, match="sonnet"):
        load_config(config_path=config_file)


def test_unrecognised_key_in_defaults_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[defaults]", "[defaults]\nsomething_unexpected = 1"
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="something_unexpected"):
        load_config(config_path=config_file)


def test_unrecognised_key_in_model_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\nsomething_unexpected = 1"
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="something_unexpected"):
        load_config(config_path=config_file)


@pytest.mark.parametrize(
    "key",
    ["api_key", "apiKey", "api-key", "secret", "auth_token", "password", "credential_path"],
)
def test_secret_like_key_in_model_raises_with_no_secrets_message(
    tmp_path: Path, key: str
) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", f'[models.sonnet]\n{key} = "x"'
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="config holds no secrets"):
        load_config(config_path=config_file)


def test_secret_like_key_in_defaults_raises_with_no_secrets_message(
    tmp_path: Path,
) -> None:
    content = MINIMAL_CONFIG.replace(
        "[defaults]", '[defaults]\napi_key = "x"'
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="config holds no secrets"):
        load_config(config_path=config_file)


def test_negative_price_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace("input_per_mtok = 3.00", "input_per_mtok = -1.0")
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="input_per_mtok"):
        load_config(config_path=config_file)


def test_non_positive_timeout_seconds_in_defaults_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace("timeout_seconds = 30.0", "timeout_seconds = 0")
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config(config_path=config_file)


def test_non_positive_max_output_tokens_in_defaults_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace("max_output_tokens = 1024", "max_output_tokens = 0")
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="max_output_tokens"):
        load_config(config_path=config_file)


def test_non_positive_timeout_seconds_on_model_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\ntimeout_seconds = -5"
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_config(config_path=config_file)


def test_non_positive_max_output_tokens_on_model_raises(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\nmax_output_tokens = 0"
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="max_output_tokens"):
        load_config(config_path=config_file)


def test_zero_models_defined_raises(tmp_path: Path) -> None:
    content = """
[defaults]
timeout_seconds = 30.0
max_output_tokens = 1024
"""
    config_file = _write(tmp_path / "llmclient.toml", content)

    with pytest.raises(ConfigError, match="no models"):
        load_config(config_path=config_file)


def test_invalid_toml_raises_config_error(tmp_path: Path) -> None:
    config_file = _write(tmp_path / "llmclient.toml", "this is not [valid toml")

    with pytest.raises(ConfigError):
        load_config(config_path=config_file)


# ---------------------------------------------------------------------------
# ledger_path
# ---------------------------------------------------------------------------


def test_ledger_path_off_sentinel_is_none(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[defaults]", '[defaults]\nledger_path = "off"'
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    config = load_config(config_path=config_file)

    assert config.defaults.ledger_path is None


def test_ledger_path_absent_defaults_to_local_share(tmp_path: Path) -> None:
    config_file = _write(tmp_path / "llmclient.toml", MINIMAL_CONFIG)

    config = load_config(config_path=config_file)

    assert config.defaults.ledger_path == Path(
        "~/.local/share/llmclient/ledger.jsonl"
    ).expanduser()


def test_ledger_path_is_expanduser_ed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    content = MINIMAL_CONFIG.replace(
        "[defaults]", '[defaults]\nledger_path = "~/custom/ledger.jsonl"'
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    config = load_config(config_path=config_file)

    assert config.defaults.ledger_path == tmp_path / "custom" / "ledger.jsonl"


# ---------------------------------------------------------------------------
# Decimal exactness
# ---------------------------------------------------------------------------


def test_price_is_decimal_exact_not_binary_float(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\ncache_read_per_mtok = 0.30"
    )
    config_file = _write(tmp_path / "llmclient.toml", content)

    config = load_config(config_path=config_file)

    price = config.models["sonnet"].cache_read_per_mtok
    assert price == Decimal("0.3")
    # Decimal(0.30) (from the raw float, not its str()) carries binary
    # floating-point noise and would NOT equal the exact decimal.
    assert price != Decimal(0.30)


def test_absent_cache_prices_default_to_zero(tmp_path: Path) -> None:
    config_file = _write(tmp_path / "llmclient.toml", MINIMAL_CONFIG)

    config = load_config(config_path=config_file)

    model = config.models["sonnet"]
    assert model.cache_read_per_mtok == Decimal(0)
    assert model.cache_write_per_mtok == Decimal(0)


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


def _load(tmp_path: Path, content: str) -> Config:
    config_file = _write(tmp_path / "llmclient.toml", content)
    return load_config(config_path=config_file)


def test_resolve_unknown_alias_lists_known_aliases(tmp_path: Path) -> None:
    config = _load(tmp_path, MINIMAL_CONFIG)

    with pytest.raises(ConfigError, match="sonnet"):
        config.resolve("nonexistent")


def test_resolve_falls_back_to_defaults_when_no_overrides(tmp_path: Path) -> None:
    config = _load(tmp_path, MINIMAL_CONFIG)

    resolved = config.resolve("sonnet")

    assert resolved.timeout_seconds == 30.0
    assert resolved.max_output_tokens == 1024
    assert resolved.alias == "sonnet"
    assert resolved.vendor == "anthropic"
    assert resolved.model_id == "claude-sonnet-4-6"
    assert resolved.prices is config.models["sonnet"]


def test_resolve_uses_model_level_override_over_defaults(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\ntimeout_seconds = 60.0\nmax_output_tokens = 2048"
    )
    config = _load(tmp_path, content)

    resolved = config.resolve("sonnet")

    assert resolved.timeout_seconds == 60.0
    assert resolved.max_output_tokens == 2048


def test_resolve_call_argument_wins_over_model_and_defaults(tmp_path: Path) -> None:
    content = MINIMAL_CONFIG.replace(
        "[models.sonnet]", "[models.sonnet]\ntimeout_seconds = 60.0\nmax_output_tokens = 2048"
    )
    config = _load(tmp_path, content)

    resolved = config.resolve("sonnet", timeout_seconds=5.0, max_output_tokens=128)

    assert resolved.timeout_seconds == 5.0
    assert resolved.max_output_tokens == 128
