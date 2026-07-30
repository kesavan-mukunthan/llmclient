# llmclient — founding spec (v0.1 draft)

*Standalone, reusable LLM client package: `llmclient`.*
*Drafted 2026-07-30; this document is self-contained and carries everything
the new project needs.*

## 1. Purpose

One thin, vendor-agnostic layer through which all of the author's projects
call LLM APIs, with runtime model/vendor selection by alias and a per-call
spend ledger. Dual purpose: (a) working infrastructure shared across projects
(FolioLens commentary first); (b) a portfolio artifact demonstrating
architecture judgment and LLM-API familiarity — clean seams, deliberate scope
exclusions, provenance-aware design.

Explicitly NOT a framework. No agent loops, no prompt management, no retrieval.
Callers own their prompts, retries-on-content, and validation. `llmclient` owns
transport, selection, usage capture, and cost.

## 2. Scope

**In (v0.1):**
- Sync, non-streaming chat completion against a config-declared model alias
- Vendor adapters: `anthropic`, `google` (Gemini via `google-genai`)
- Per-call usage capture (tokens incl. cache read/write where reported),
  cost computed from config pricing, latency
- Append-only spend ledger (JSONL) + `llmclient spend` aggregation CLI
- Typed `Completion` result; typed, vendor-mapped exceptions

**Out (recorded, deliberate — revisit only on demonstrated need):**
- Streaming, async, tool/function calling, structured-output modes,
  multimodal inputs, prompt caching control, batch APIs
- Fallback routing / retry-on-transport (callers decide; a transport retry
  that silently switches models would corrupt caller provenance)
- Server/proxy mode, multi-tenancy, key vaulting beyond env vars

## 3. Config

TOML, located via `LLMCLIENT_CONFIG` env var, else `./llmclient.toml`, else
`~/.config/llmclient/config.toml` (first found wins; no merging).

```toml
[defaults]
timeout_seconds = 30.0
max_output_tokens = 1024
ledger_path = "~/.local/share/llmclient/ledger.jsonl"   # "off" disables

[models.sonnet]                    # alias — callers use only this
vendor    = "anthropic"
model_id  = "claude-sonnet-4-6"
input_per_mtok  = 3.00             # USD; used for cost computation only
output_per_mtok = 15.00
cache_read_per_mtok  = 0.30       # optional; 0 if absent
cache_write_per_mtok = 3.75       # optional

[models.haiku]
vendor    = "anthropic"
model_id  = "claude-haiku-4-5-20251001"
input_per_mtok  = 1.00
output_per_mtok = 5.00

[models.flash]
vendor    = "google"
model_id  = "gemini-2.5-flash"
input_per_mtok  = 0.30
output_per_mtok = 2.50
```

Rules:
- Pricing lives in config, never in code — prices change; releases shouldn't.
- Aliases are the only caller-facing names. `model_id` never appears in
  caller code.
- Per-model `timeout_seconds` / `max_output_tokens` override defaults.
- API keys via env only: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`. Config
  never contains secrets; loader hard-fails if it finds a key-like field.
- Unknown vendor string in config → config-load error, not call-time error.

## 4. Core API

```python
from llmclient import Client, Completion

client = Client()                      # loads config per §3
result: Completion = client.complete(
    alias="sonnet",                    # runtime switch — the whole point
    system="...",
    messages=[{"role": "user", "content": "..."}],
    project="foliolens",               # ledger tag, required
    max_output_tokens=None,            # optional per-call overrides
    timeout_seconds=None,
)
```

`Completion` (frozen dataclass):

```python
text: str                  # concatenated text blocks
alias: str                 # as requested
vendor: str
model_id: str              # resolved — persist THIS for provenance
input_tokens: int
output_tokens: int
cache_read_tokens: int     # 0 where vendor doesn't report
cache_write_tokens: int
cost_usd: Decimal          # computed from config pricing; Decimal, not float
latency_seconds: float
requested_at: str          # ISO-8601 UTC
raw_usage: dict            # vendor-verbatim usage block, for audit
```

Errors: `LLMClientError` base; `ConfigError`, `AuthError`, `RateLimitError`,
`TimeoutError`, `VendorError` (carries vendor + status + body excerpt).
Adapters map vendor SDK exceptions into these; SDK exception types never
cross the package boundary.

Message shape: the Anthropic-style `[{"role","content"}]` list with a
separate `system` string is the package's canonical shape; adapters
translate (Gemini: system → `system_instruction`, roles remapped). Only
`user`/`assistant` roles in v0.1; anything else → `ConfigError`-class
failure at call time, loud.

## 5. Adapter protocol

```python
class Adapter(Protocol):
    vendor: ClassVar[str]
    def complete(self, model_id: str, system: str,
                 messages: list[dict[str, str]],
                 max_output_tokens: int,
                 timeout_seconds: float) -> AdapterResult: ...
```

`AdapterResult` = text + raw usage dict + normalised token counts. Registry
is a plain dict `{vendor: adapter_cls}` populated at import; adding a vendor
= one module + one registry line. SDKs imported lazily inside adapters so
installing `llmclient` doesn't require every vendor's SDK (optional extras:
`llmclient[anthropic]`, `llmclient[google]`).

Cost is computed in `Client`, not adapters — adapters report tokens,
config prices them. One place, one rounding rule (Decimal, 6 dp).

## 6. Spend ledger

Append-only JSONL, one object per call, written after every completion
(success or vendor error with usage, when available):

```json
{"ts": "...", "project": "foliolens", "alias": "sonnet",
 "vendor": "anthropic", "model_id": "claude-sonnet-4-6",
 "input_tokens": 5210, "output_tokens": 287,
 "cache_read_tokens": 0, "cache_write_tokens": 0,
 "cost_usd": "0.019935", "latency_seconds": 3.41, "ok": true}
```

- Never contains prompt or response text — spend data, not conversation
  logs. This is a privacy/size decision; record it in the module docstring.
- Ledger write failure logs a warning, never fails the call.
- `llmclient spend [--project X] [--since YYYY-MM-DD] [--by alias|project|day]`
  aggregates. Costs summed as Decimal.
- JSONL not SQLite: append-only, greppable, trivially concatenable across
  machines; revisit only if aggregation actually gets slow.

## 7. Repo layout & tooling

Mirror the FolioLens conventions (deliberate — one house style):

```
llmclient/
  pyproject.toml          # hatchling, src-layout, uv-managed
  llmclient.toml.example
  src/llmclient/
    __init__.py           # Client, Completion, errors — the whole public API
    config.py             # load + validate (fail loud, typed)
    client.py             # alias resolution, cost, ledger hook
    ledger.py
    errors.py
    adapters/
      __init__.py         # registry
      anthropic.py
      google.py
    cli.py                # `llmclient spend`
  tests/
```

- Python ≥3.12, `uv`, `ruff`, `mypy --strict`, `pytest`; CI = single `gate`
  job (ruff → mypy → pytest), branch protection on `main` with `gate`
  required, review-required merges (owner's click).
- Testing: adapters covered by fake-SDK unit tests (recorded response
  shapes as fixtures, incl. usage blocks); zero network in CI. One
  opt-in live smoke test per vendor behind `LLMCLIENT_LIVE_TEST=1`, excluded
  from `gate`.
- Versioning: semver from 0.1.0; `Completion` and config schema changes
  are breaking after 1.0. Installed into consumer projects as a git
  dependency (`uv add git+https://github.com/kesavan-mukunthan/llmclient`) — no PyPI
  until a second consumer exists.

## 8. FolioLens integration contract (later, separate FolioLens PR)

- A ~10-line shim in FolioLens satisfies the existing
  `CommentaryTransport = Callable[[str, list[dict[str,str]]], str]`
  signature by calling `client.complete(alias=..., project="foliolens")`
  and returning `.text`.
- FolioLens pins the alias per batch (CLI arg or its own config) and
  persists `Completion.model_id` into each commentary block — cohort
  consistency and provenance stay FolioLens's responsibility, exactly as
  today. `llmclient` provides the switch; the caller provides the discipline.
- **Hard prerequisite** already queued on the FolioLens board: staleness
  key in `runner.py` becomes `(model, prompt_version)` (rides the
  commentary-v6 PR). Integration must not land before it.
- Integration takes a FolioLens queue slot; `llmclient` development itself
  does not (separate repo, separate project).

## 9. Open questions (decide in the new project, not silently)

1. Ledger default location: per-machine (`~/.local/share`) vs per-project
   (`./.llmclient/ledger.jsonl`). Per-machine draft-preferred: spend is an
   account-level concern; `project` field already segments it.
2. Whether `complete()` grows a `metadata: dict` passthrough into the
   ledger (e.g. FolioLens could tag `amfi_code`). Cheap, but scope-creep
   adjacent; default no.
3. Gemini usage nuances (thinking-token accounting on 2.5-series) —
   resolve against the live API during adapter work, record in adapter
   docstring.
4. Whether vendor-side server tools (web search etc.) stay permanently
   out or become a 0.2 concern. Draft: permanently out; that's framework
   territory.

## 10. Working conventions for the new project

- Live repo is source of truth once it exists; specs precede code; red
  tests before fixes; every merge is a human click.
- Division of labour: architecture/spec/PR review in the project chat;
  mechanical implementation via Claude Code sessions against exact
  manifests.
- This spec is the founding project-knowledge document; supersede sections
  by editing the repo copy (`docs/SPEC.md`), not by chat memory.
