# llmclient

## Purpose

`llmclient` is one thin, vendor-agnostic layer through which all of the
author's projects call LLM APIs, with runtime model/vendor selection by
alias and a per-call spend ledger.

It is explicitly **not** a framework: no agent loops, no prompt
management, no retrieval. Callers own their prompts, retries-on-content,
and validation. `llmclient` owns transport, model selection, usage
capture, and cost.

See [`docs/SPEC.md`](docs/SPEC.md) for the full founding spec.

## Non-goals

- Streaming, async, tool/function calling, structured-output modes,
  multimodal inputs, prompt caching control, batch APIs.
- Fallback routing / retry-on-transport — callers decide; a transport
  retry that silently switches models would corrupt caller provenance.
- Server/proxy mode, multi-tenancy, key vaulting beyond env vars.
