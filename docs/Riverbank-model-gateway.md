# Model gateway: concurrency cap, call budget, circuit breaker

Protects traffic going into the external models (OpenAI, OpenRouter, Jev) and makes the existing fallbacks kick in fast when a provider is down. Scope is #1 (shared model budget) and #2 (shared circuit breaker) from the Valkey discussion. The LightGBM risk model runs in-process and is not gated.

## Problem today

- Every model call is synchronous inside the request. Nothing caps how many run at once or how many run per minute, per process or across processes.
- When a provider is down, every request waits for the full timeout (memo 15s, Copilot 20s, Jev 10–20s, plus retries) before the fallback runs. Under load this ties up the uvicorn thread pool and DB connections held by the request.
- The fallbacks already exist: `FallbackMemoWriter` writes the template memo, `CaseAdvisor` answers with `FactsReply`, `JevCicGate` closes the gate, the job queue retries 5xx. They just trigger too late.

## Design

One class, `ModelGate` in `api/services/modelgate.py`, wraps every outbound model call:

```
caller ─▶ ModelGate.call(provider, fn, is_failure)
             1. breaker open?           → GateRejected("MODEL_CIRCUIT_OPEN")
             2. take a concurrency slot → wait up to wait_seconds, else GateRejected("MODEL_BUSY")
             3. count call this minute  → over per_minute: GateRejected("MODEL_RATE_LIMITED")
             4. fn()
                  ok                    → clear failure count
                  is_failure(exc)       → count failure, trip breaker at threshold
             5. release slot (always)
```

State lives in a `KVClient` from `api/kv/`: `MemoryKVClient` (per process, default) or `ValkeyClient` (shared across workers and containers). Same code, store picked by config.

### Providers

The gate key is the provider, not the purpose, because quotas and outages are per provider.

| Provider key | Used by |
|---|---|
| `openai` | `OpenAICompatibleClient` when `LLM_PROVIDER` / `STATEMENT_OCR_PROVIDER` is `openai` |
| `openrouter` | same, when the provider is `openrouter` |
| `jev` | `jev.decide()`: CIC gate, input guard, payout classifier |

Jev goes through OpenRouter but has its own key, so a Jev outage does not block the memo LLM and the other way round.

### Concurrency slots

N slot keys per provider: `gate:slot:{provider}:{0..N-1}`. Acquire = `SET key token NX EX lease_seconds` on the first free one. Release = delete only if the value is still our token (`KVClient.delete_if`, a Lua compare-and-delete on Valkey). If a process dies mid-call, its slot frees itself when the lease expires. No counter that can leak.

When all slots are taken the caller polls every 100 ms up to `wait_seconds` (default 2s), then gets `MODEL_BUSY`. A short wait smooths bursts without holding a request for the whole model timeout.

### Calls per minute

Fixed window: `INCR gate:rpm:{provider}:{epoch_minute}` with a 60s TTL. Over the limit → `MODEL_RATE_LIMITED`, slot released. A fixed window can let up to 2× through across a minute boundary; acceptable for a budget guard, and it needs no new KV primitives. One gate call counts once even if the OpenAI SDK retries inside it.

### Circuit breaker

- Failure: `INCR gate:fail:{provider}` with TTL `breaker_window_seconds`. At `breaker_failures` → `SET gate:open:{provider} EX breaker_open_seconds` and extend the failure key to `open + window`.
- While `gate:open` exists every call is rejected immediately.
- When it expires calls flow again (half-open). The failure count is still at the threshold, so one more failure re-opens the breaker; one success deletes the count and closes it.

Only transient errors count as failures, so a bad prompt or schema mismatch never opens the breaker:

| Caller | Counts as failure |
|---|---|
| LLM | `APIConnectionError` (includes timeout), `RateLimitError`, `InternalServerError` (5xx) |
| Jev | `httpx.TransportError`, HTTP 429, HTTP 5xx |

### Store failure: fail open

Every KV operation is wrapped. If Valkey is unreachable (`ValkeyClientError`) the gate logs a warning and lets the call through without a slot or count. Losing Valkey degrades to today's behaviour, it never blocks model calls.

## What each caller sees

Rejections reuse the error paths that exist today, so no caller logic changes.

| Caller | Rejected or breaker open → |
|---|---|
| Memo (`LlmMemoWriter`) | `LlmError(code)` → `FallbackMemoWriter` writes the template, code recorded in `validation_errors` |
| Copilot ask (`CaseAdvisor`) | `LlmError` → `FactsReply` template answer |
| Copilot extract (`PlanExtractor`) | `LlmError` → 503 `EXTRACTION_UNAVAILABLE` with the code in details (no fallback today) |
| Statement OCR (`VisionStatementParser`) | `LlmError` → existing OCR error handling |
| CIC gate (`JevCicGate`) | exception → gate closed, CIC not queried, `error=GateRejected` |
| Input guard (`JevInputGuard`) | `GuardUnavailable` → 503 `GUARD_UNAVAILABLE` |
| Payout classifier (`JevPayoutClassifier`) | 503 `STATEMENT_CLASSIFIER_UNAVAILABLE` → job queue retries (5xx) |

Codes: `MODEL_CIRCUIT_OPEN`, `MODEL_BUSY`, `MODEL_RATE_LIMITED`.

## Wiring

- `llm.py`: `OpenAICompatibleClient` takes `gate` and `provider`. `_create` runs the SDK call through `gate.call`. `build_llm_client` passes `model_gate()` and the provider. `CachedLlmClient` stays outside, so cache hits use no budget.
- `jev.py`: `decide()` runs the HTTP post through `model_gate().call("jev", ...)`. Callers that inject `decide=` in tests are untouched.
- `model_gate()` is an `lru_cache` singleton built from config, like `limiter()` in `routers/limits.py`.
- `kv/client.py`: add `delete_if(key, value)`; import `valkey` lazily (same as `llm.py` does for `openai`) so memory mode needs no Valkey package.

## Config

All env reads stay in `config.py`.

| Env | Default | Meaning |
|---|---|---|
| `RIVERBANK_MODEL_GATE` | `on` | `off` = calls pass straight through |
| `RIVERBANK_MODEL_GATE_STORE` | `RIVERBANK_KV_STORE` (`memory`) | `memory` per process, `valkey` shared |
| `RIVERBANK_MODEL_GATE_<PROVIDER>_CONCURRENCY` | 8 | slots per provider, 0 = no cap |
| `RIVERBANK_MODEL_GATE_<PROVIDER>_PER_MINUTE` | 120 | calls per minute, 0 = no cap |
| `RIVERBANK_MODEL_GATE_BREAKER_FAILURES` | 5 | failures in the window that open the breaker, 0 = no breaker |
| `RIVERBANK_MODEL_GATE_BREAKER_WINDOW_SECONDS` | 60 | failure counting window |
| `RIVERBANK_MODEL_GATE_BREAKER_OPEN_SECONDS` | 30 | how long the breaker stays open |
| `RIVERBANK_MODEL_GATE_WAIT_SECONDS` | 2 | max wait for a free slot |
| `RIVERBANK_MODEL_GATE_LEASE_SECONDS` | 120 | slot lease, longer than timeout × (retries + 1) |

With `memory` and `--workers 2` every limit is per process, so the real cap is 2×. Set `RIVERBANK_MODEL_GATE_STORE=valkey` for exact shared limits.

## Tests (`api/tests/test_modelgate.py`, no network)

`MemoryKVClient` with a fake clock and fake sleep:

- slots: N+1th concurrent call gets `MODEL_BUSY`; slot is freed after success and after an exception; a waiter gets the slot when it frees within `wait_seconds`; an expired lease frees itself
- per minute: call N+1 in the same minute is rejected, next minute passes
- breaker: opens after N transient failures, rejects while open, half-open re-trips on one failure, success closes it; non-transient errors do not count
- fail open: a store that raises `ValkeyClientError` lets calls through
- disabled gate calls `fn` directly
- `OpenAICompatibleClient` maps `GateRejected` to `LlmError(code)`; `FallbackMemoWriter` records `MODEL_CIRCUIT_OPEN`
- `jev.transient` classifies httpx errors

## Not in scope

- A queue for waiting calls beyond `wait_seconds` (Kafka or the job queue would be the place).
- Per-seller fairness inside the model budget. The HTTP rate limiter already caps each seller.
- Moving the HTTP rate limiter to Valkey.
- Metrics export. Trips and rejections are logged on `riverbank.modelgate`.
