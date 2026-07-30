"""Config loading and validation for llmclient.

TOML is parsed with the stdlib `tomllib`. Discovery order (first found wins,
no merging): an explicit `config_path` argument (highest precedence — skips
discovery entirely), then `$LLMCLIENT_CONFIG`, then `./llmclient.toml`, then
`~/.config/llmclient/config.toml`. Pricing is never read from a `float`
directly (`Decimal(str(value))`, never `Decimal(value)`) to avoid binary
floating-point noise in cost figures.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from llmclient.errors import ConfigError

# M4 replaces this with the adapter registry (one module + one registry
# line per vendor); hardcoded here since adapters don't exist yet.
_VALID_VENDORS = frozenset({"anthropic", "google"})

_SECRET_KEY_PATTERN = re.compile(r"api.?key|secret|token|password|credential", re.IGNORECASE)

_ROOT_ALLOWED_KEYS = frozenset({"defaults", "models"})

_DEFAULTS_ALLOWED_KEYS = frozenset({"timeout_seconds", "max_output_tokens", "ledger_path"})
_DEFAULTS_REQUIRED_KEYS = ("timeout_seconds", "max_output_tokens")

_MODEL_ALLOWED_KEYS = frozenset(
    {
        "vendor",
        "model_id",
        "input_per_mtok",
        "output_per_mtok",
        "cache_read_per_mtok",
        "cache_write_per_mtok",
        "timeout_seconds",
        "max_output_tokens",
    }
)
_MODEL_REQUIRED_KEYS = ("vendor", "model_id", "input_per_mtok", "output_per_mtok")

_DEFAULT_LEDGER_PATH = "~/.local/share/llmclient/ledger.jsonl"
_ENV_VAR = "LLMCLIENT_CONFIG"
_PROJECT_CONFIG_PATH = Path("llmclient.toml")
_USER_CONFIG_PATH = Path("~/.config/llmclient/config.toml")


@dataclass(frozen=True)
class Defaults:
    """Package-wide fallbacks, used when a model doesn't override them."""

    timeout_seconds: float
    max_output_tokens: int
    ledger_path: Path | None  # None means the ledger is off


@dataclass(frozen=True)
class ModelConfig:
    """A single `[models.<alias>]` table, as declared in config."""

    alias: str
    vendor: str
    model_id: str
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_read_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    timeout_seconds: float | None
    max_output_tokens: int | None


@dataclass(frozen=True)
class Resolved:
    """A model alias with all overrides applied — no `None`s left."""

    alias: str
    vendor: str
    model_id: str
    prices: ModelConfig
    timeout_seconds: float
    max_output_tokens: int


@dataclass(frozen=True)
class Config:
    defaults: Defaults
    models: Mapping[str, ModelConfig]
    source_path: Path

    def resolve(
        self,
        alias: str,
        *,
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Resolved:
        """Resolve `alias` to a fully-populated `Resolved`. No I/O.

        Three-level lookup per field, first non-None wins: the call
        argument, then the alias's own config value, then `[defaults]`.
        """
        model = self.models.get(alias)
        if model is None:
            known = ", ".join(sorted(self.models)) or "(none)"
            raise ConfigError(f"unknown model alias {alias!r}; known aliases: {known}")

        resolved_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else model.timeout_seconds
            if model.timeout_seconds is not None
            else self.defaults.timeout_seconds
        )
        resolved_max_output_tokens = (
            max_output_tokens
            if max_output_tokens is not None
            else model.max_output_tokens
            if model.max_output_tokens is not None
            else self.defaults.max_output_tokens
        )

        return Resolved(
            alias=model.alias,
            vendor=model.vendor,
            model_id=model.model_id,
            prices=model,
            timeout_seconds=resolved_timeout,
            max_output_tokens=resolved_max_output_tokens,
        )


def load_config(config_path: str | Path | None = None) -> Config:
    """Load and validate a `Config`.

    `config_path`, when given, is the highest-precedence source and skips
    discovery entirely — a missing or invalid path is a `ConfigError`.
    Otherwise config is discovered per the module docstring's order.
    """
    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        return _load_from_path(path)

    return _load_via_discovery()


def _load_via_discovery() -> Config:
    env_value = os.environ.get(_ENV_VAR)
    if env_value is not None:
        env_path = Path(env_value).expanduser()
        if not env_path.is_file():
            raise ConfigError(f"${_ENV_VAR} is set to {env_path}, but that file does not exist")
        return _load_from_path(env_path)

    if _PROJECT_CONFIG_PATH.is_file():
        return _load_from_path(_PROJECT_CONFIG_PATH)

    user_path = _USER_CONFIG_PATH.expanduser()
    if user_path.is_file():
        return _load_from_path(user_path)

    raise ConfigError(
        "no llmclient config found; checked "
        f"${_ENV_VAR} (unset), {_PROJECT_CONFIG_PATH}, and {user_path}"
    )


def _load_from_path(path: Path) -> Config:
    path = path.resolve()
    with path.open("rb") as f:
        try:
            raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    _check_allowed_keys(raw, _ROOT_ALLOWED_KEYS, where=f"{path}: config root")

    defaults = _parse_defaults(raw.get("defaults", {}))

    models_raw = raw.get("models", {})
    if not isinstance(models_raw, dict):
        raise ConfigError(f"{path}: [models] must be a table of `[models.<alias>]` tables")
    if not models_raw:
        raise ConfigError(f"{path}: no models defined; add at least one [models.<alias>] table")

    models = {alias: _parse_model(alias, model_raw) for alias, model_raw in models_raw.items()}

    return Config(defaults=defaults, models=models, source_path=path)


def _check_allowed_keys(raw: Mapping[str, object], allowed: frozenset[str], *, where: str) -> None:
    unknown = set(raw) - allowed
    # Secret-like keys are always unrecognised (none of the allowlisted
    # field names match the pattern); check them first for a message that
    # points at env vars instead of the generic "unrecognised key" one.
    for key in sorted(unknown):
        if _SECRET_KEY_PATTERN.search(key):
            raise ConfigError(
                f"{where} field {key!r} looks like a secret; config holds no secrets — "
                "use environment variables instead (e.g. ANTHROPIC_API_KEY, GEMINI_API_KEY)"
            )
    if unknown:
        raise ConfigError(f"{where} has unrecognised key(s): {', '.join(sorted(unknown))}")


def _validate_timeout_seconds(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where} field 'timeout_seconds' must be a number, got {value!r}")
    if value <= 0:
        raise ConfigError(f"{where} field 'timeout_seconds' must be positive, got {value!r}")
    return float(value)


def _validate_max_output_tokens(value: object, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} field 'max_output_tokens' must be an integer, got {value!r}")
    if value <= 0:
        raise ConfigError(f"{where} field 'max_output_tokens' must be positive, got {value!r}")
    return value


def _parse_defaults(raw: object) -> Defaults:
    if not isinstance(raw, dict):
        raise ConfigError("[defaults] must be a table")

    _check_allowed_keys(raw, _DEFAULTS_ALLOWED_KEYS, where="[defaults]")

    missing = [key for key in _DEFAULTS_REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"[defaults] missing required field(s): {', '.join(missing)}")

    timeout_seconds = _validate_timeout_seconds(raw["timeout_seconds"], where="[defaults]")
    max_output_tokens = _validate_max_output_tokens(raw["max_output_tokens"], where="[defaults]")
    ledger_path = _parse_ledger_path(raw.get("ledger_path"))

    return Defaults(
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
        ledger_path=ledger_path,
    )


def _parse_ledger_path(raw: object) -> Path | None:
    if raw is None:
        return Path(_DEFAULT_LEDGER_PATH).expanduser()
    if raw == "off":
        return None
    if not isinstance(raw, str):
        raise ConfigError(f"[defaults] field 'ledger_path' must be a string, got {raw!r}")
    return Path(raw).expanduser()


def _parse_price(value: object, *, alias: str, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"model {alias!r} field {field!r} must be a number, got {value!r}")
    price = Decimal(str(value))
    if price < 0:
        raise ConfigError(f"model {alias!r} field {field!r} must not be negative, got {value!r}")
    return price


def _parse_model(alias: str, raw: object) -> ModelConfig:
    where = f"model {alias!r}"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")

    _check_allowed_keys(raw, _MODEL_ALLOWED_KEYS, where=where)

    missing = [key for key in _MODEL_REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"{where} missing required field(s): {', '.join(missing)}")

    vendor = raw["vendor"]
    if vendor not in _VALID_VENDORS:
        vendors = ", ".join(sorted(_VALID_VENDORS))
        raise ConfigError(f"{where} field 'vendor' is {vendor!r}; must be one of {vendors}")

    model_id = raw["model_id"]
    if not isinstance(model_id, str) or not model_id:
        raise ConfigError(f"{where} field 'model_id' must be a non-empty string")

    input_per_mtok = _parse_price(raw["input_per_mtok"], alias=alias, field="input_per_mtok")
    output_per_mtok = _parse_price(raw["output_per_mtok"], alias=alias, field="output_per_mtok")
    cache_read_per_mtok = _parse_price(
        raw.get("cache_read_per_mtok", 0), alias=alias, field="cache_read_per_mtok"
    )
    cache_write_per_mtok = _parse_price(
        raw.get("cache_write_per_mtok", 0), alias=alias, field="cache_write_per_mtok"
    )

    timeout_seconds: float | None = None
    if "timeout_seconds" in raw:
        timeout_seconds = _validate_timeout_seconds(raw["timeout_seconds"], where=where)

    max_output_tokens: int | None = None
    if "max_output_tokens" in raw:
        max_output_tokens = _validate_max_output_tokens(raw["max_output_tokens"], where=where)

    return ModelConfig(
        alias=alias,
        vendor=vendor,
        model_id=model_id,
        input_per_mtok=input_per_mtok,
        output_per_mtok=output_per_mtok,
        cache_read_per_mtok=cache_read_per_mtok,
        cache_write_per_mtok=cache_write_per_mtok,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )
