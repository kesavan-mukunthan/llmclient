"""Append-only spend ledger for llmclient.

This file records spend — never prompt or response text. That is a
deliberate privacy and size decision: the ledger exists to answer "how
much did this call cost," not to serve as a conversation log, so keeping
prompt/response text out of it keeps the file small, greppable, and safe
to retain indefinitely.

Row shape and semantics are defined in docs/SPEC.md §6. This module owns
appending and reading rows only; aggregation, filtering, multi-file/glob
reads, and cost computation belong to the `llmclient spend` CLI (M8).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

_LOGGER = logging.getLogger("llmclient.ledger")

_REQUIRED_KEYS = (
    "ts",
    "project",
    "stage",
    "alias",
    "vendor",
    "model_id",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "cost_usd",
    "latency_seconds",
    "ok",
)
_ALLOWED_KEYS = frozenset(_REQUIRED_KEYS) | {"error"}
_STRING_KEYS = ("ts", "project", "stage", "alias", "vendor", "model_id")
_INT_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One append-only ledger entry — one row per completion attempt."""

    ts: str
    project: str
    stage: str
    alias: str
    vendor: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal
    latency_seconds: float
    ok: bool
    error: str | None = None

    def __post_init__(self) -> None:
        if self.error is not None and self.ok is True:
            raise ValueError("a row cannot be both ok=True and carry an error")

    def to_json_obj(self) -> dict[str, object]:
        """Render this row as a JSON-ready dict, field order per §6."""
        obj: dict[str, object] = {
            "ts": self.ts,
            "project": self.project,
            "stage": self.stage,
            "alias": self.alias,
            "vendor": self.vendor,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": str(self.cost_usd),
            "latency_seconds": self.latency_seconds,
            "ok": self.ok,
        }
        if self.error is not None:
            obj["error"] = self.error
        return obj

    @classmethod
    def from_json_obj(cls, obj: object) -> LedgerRow | None:
        """Parse one decoded JSON value into a `LedgerRow`, or `None`.

        Returns `None` for anything malformed instead of raising — not a
        dict, a missing or extra key, a wrong-typed field, an unparseable
        `cost_usd`, or a bool standing in for an int — so `read()` can
        skip bad lines without failing the whole file.
        """
        if not isinstance(obj, dict):
            return None

        if set(obj) - _ALLOWED_KEYS:
            return None

        if any(key not in obj for key in _REQUIRED_KEYS):
            return None

        strings: dict[str, str] = {}
        for key in _STRING_KEYS:
            value = obj[key]
            if not isinstance(value, str):
                return None
            strings[key] = value

        ints: dict[str, int] = {}
        for key in _INT_KEYS:
            value = obj[key]
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            ints[key] = value

        cost_raw = obj["cost_usd"]
        if not isinstance(cost_raw, str) or "_" in cost_raw:
            return None
        try:
            cost_usd = Decimal(cost_raw)
        except InvalidOperation:
            return None
        if not cost_usd.is_finite():
            return None

        latency_raw = obj["latency_seconds"]
        if isinstance(latency_raw, bool) or not isinstance(latency_raw, (int, float)):
            return None

        ok = obj["ok"]
        if not isinstance(ok, bool):
            return None

        error_raw = obj.get("error")
        if error_raw is not None and not isinstance(error_raw, str):
            return None
        if error_raw is not None and ok is True:
            return None

        return cls(
            ts=strings["ts"],
            project=strings["project"],
            stage=strings["stage"],
            alias=strings["alias"],
            vendor=strings["vendor"],
            model_id=strings["model_id"],
            input_tokens=ints["input_tokens"],
            output_tokens=ints["output_tokens"],
            cache_read_tokens=ints["cache_read_tokens"],
            cache_write_tokens=ints["cache_write_tokens"],
            cost_usd=cost_usd,
            latency_seconds=float(latency_raw),
            ok=ok,
            error=error_raw,
        )


@dataclass(frozen=True)
class ReadResult:
    """Result of reading a ledger file: parsed rows plus bad line numbers."""

    rows: list[LedgerRow]
    malformed_lines: list[int]


def append(path: Path | None, row: LedgerRow) -> None:
    """Append `row` to the ledger at `path` as one JSON line.

    `path=None` means the ledger is disabled (config `ledger_path =
    "off"`) — a no-op. Exactly one `write()` call is made per row so
    concurrent appends interleave at worst at line granularity. Write
    failures (missing/unwritable directory, full disk, permissions) are
    logged and swallowed — a ledger write must never fail the caller's
    completion.
    """
    if path is None:
        return

    line = json.dumps(row.to_json_obj(), ensure_ascii=True, separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        _LOGGER.warning("failed to append ledger entry to %s", path, exc_info=True)


def read(path: Path) -> ReadResult:
    """Read all rows from the ledger at `path`.

    A missing file is treated as an empty ledger, not an error. Any line
    that isn't valid JSON, or doesn't decode to a well-formed `LedgerRow`,
    is skipped and its 1-based line number recorded in `malformed_lines`;
    this function never raises on file content. Undecodable bytes are
    replaced (U+FFFD) rather than raising — the resulting line then fails
    JSON parsing or shape validation and is reported as malformed too.
    """
    if not path.is_file():
        return ReadResult(rows=[], malformed_lines=[])

    rows: list[LedgerRow] = []
    malformed_lines: list[int] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_number, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                malformed_lines.append(line_number)
                continue
            row = LedgerRow.from_json_obj(obj)
            if row is None:
                malformed_lines.append(line_number)
                continue
            rows.append(row)

    return ReadResult(rows=rows, malformed_lines=malformed_lines)
