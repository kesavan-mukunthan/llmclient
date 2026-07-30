# llmclient

## Purpose

`llmclient` is a small, vendor-agnostic client for calling different LLM
providers (starting with Anthropic and Google) from one consistent
interface, with built-in tracking of request cost so usage across models
and vendors can be measured and budgeted.

## Non-goals

- Not a general-purpose agent framework, orchestration engine, or CLI tool.
- Not a proxy server or hosted service — it's a library you import.
- Not committed to supporting every vendor or every model; vendors are
  added deliberately, behind optional extras.
- Not a replacement for vendor SDKs' advanced/streaming features on day
  one — the initial surface favors a small, predictable core over
  completeness.
