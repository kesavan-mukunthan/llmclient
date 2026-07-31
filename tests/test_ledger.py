from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from llmclient.ledger import LedgerRow, ReadResult, append, read

_JSON_KEY_SET = {
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
}


def _row(**overrides: object) -> LedgerRow:
    defaults: dict[str, object] = {
        "ts": "2026-07-31T00:00:00Z",
        "project": "foliolens",
        "stage": "commentary",
        "alias": "sonnet",
        "vendor": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "input_tokens": 5210,
        "output_tokens": 287,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": Decimal("0.019935"),
        "latency_seconds": 3.41,
        "ok": True,
        "error": None,
    }
    defaults.update(overrides)
    return LedgerRow(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------


def test_append_with_none_path_writes_nothing(tmp_path: Path) -> None:
    append(None, _row())

    assert list(tmp_path.iterdir()) == []


def test_append_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "ledger.jsonl"

    append(path, _row())

    assert path.is_file()


def test_two_appends_produce_two_lines_read_in_order(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = _row(stage="commentary")
    second = _row(stage="summary")

    append(path, first)
    append(path, second)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    result = read(path)
    assert [r.stage for r in result.rows] == ["commentary", "summary"]
    assert result.malformed_lines == []


def test_round_trip_decimal_exactness(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    row = _row(cost_usd=Decimal("0.019935"))

    append(path, row)
    result = read(path)

    assert len(result.rows) == 1
    assert result.rows[0].cost_usd == Decimal("0.019935")
    assert isinstance(result.rows[0].cost_usd, Decimal)


def test_successful_row_has_no_error_key_in_written_json(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"

    append(path, _row(ok=True, error=None))

    line = path.read_text(encoding="utf-8").splitlines()[0]
    obj = json.loads(line)
    assert "error" not in obj


def test_ok_true_with_error_raises_value_error() -> None:
    with pytest.raises(ValueError):
        _row(ok=True, error="RateLimitError")


def test_error_row_round_trips_with_class_name_preserved(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    row = _row(ok=False, error="RateLimitError")

    append(path, row)
    result = read(path)

    assert len(result.rows) == 1
    assert result.rows[0].ok is False
    assert result.rows[0].error == "RateLimitError"


def test_append_into_unwritable_directory_logs_warning_and_returns_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A regular file where a directory is expected makes `mkdir` fail with
    # an OSError regardless of the running user's privileges (unlike a
    # chmod-based permission test, which root bypasses).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    path = blocker / "ledger.jsonl"

    with caplog.at_level("WARNING", logger="llmclient.ledger"):
        # `append` is annotated -> None; capture it dynamically to assert
        # the runtime return value, not just the static type.
        result = append(path, _row())  # type: ignore[func-returns-value]

    assert result is None
    assert not path.exists()
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_written_json_key_set_matches_spec(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"

    append(path, _row(ok=True, error=None))

    line = path.read_text(encoding="utf-8").splitlines()[0]
    obj = json.loads(line)
    assert set(obj) == _JSON_KEY_SET


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_nonexistent_path_returns_empty(tmp_path: Path) -> None:
    result = read(tmp_path / "does-not-exist.jsonl")

    assert result == ReadResult(rows=[], malformed_lines=[])


def test_read_truncated_final_line_reports_malformed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    good_line = json.dumps(_row().to_json_obj(), separators=(",", ":"))
    path.write_text(good_line + "\n" + '{"ts": "2026-07-31T00:00:00Z", "proj', encoding="utf-8")

    result = read(path)

    assert len(result.rows) == 1
    assert result.malformed_lines == [2]


@pytest.mark.parametrize(
    "content",
    [
        "[1, 2, 3]",
        json.dumps(
            {
                "ts": "x",
                "project": "p",
                "stage": "s",
                "alias": "a",
                "vendor": "v",
                "model_id": "m",
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "latency_seconds": 1.0,
                "ok": True,
            }
        ),
        json.dumps(
            {
                "ts": "x",
                "project": "p",
                "stage": "s",
                "alias": "a",
                "vendor": "v",
                "model_id": "m",
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": "abc",
                "latency_seconds": 1.0,
                "ok": True,
            }
        ),
        json.dumps(
            {
                "ts": "x",
                "project": "p",
                "stage": "s",
                "alias": "a",
                "vendor": "v",
                "model_id": "m",
                "input_tokens": True,
                "output_tokens": 1,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "cost_usd": "0.01",
                "latency_seconds": 1.0,
                "ok": True,
            }
        ),
    ],
    ids=["json-list", "missing-cost_usd", "cost_usd-not-numeric", "input_tokens-bool"],
)
def test_malformed_cases_each_counted(tmp_path: Path, content: str) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(content + "\n", encoding="utf-8")

    result = read(path)

    assert result.rows == []
    assert result.malformed_lines == [1]


def test_blank_lines_are_skipped_and_not_counted_malformed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    good_line = json.dumps(_row().to_json_obj(), separators=(",", ":"))
    path.write_text(f"\n{good_line}\n   \n{good_line}\n\n", encoding="utf-8")

    result = read(path)

    assert len(result.rows) == 2
    assert result.malformed_lines == []
