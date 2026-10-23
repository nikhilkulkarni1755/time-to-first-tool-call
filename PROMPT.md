# Time-to-First-Tool-Call (TTFTC) Benchmark: Latest Claude Models

Build a Python benchmark that measures how long each model takes to emit its **first tool call** when given a large tool surface (5 connectors x ~20 tools = ~100 tools), and checks that every parameter of that call is correct.

Phase 1 scope: the five latest Claude models, funded by **$200 of Anthropic API credits**. This is a speed experiment: anything that does not change tool-call speed or the correctness of the call is out of scope (see section 9).

Do not write code until the plan is confirmed. When implementing, follow the existing codebase's style, keep changes minimal, reuse existing functions rather than duplicating them.

---

## 1. Metric

**TTFTC** = time from the request leaving the harness to the model emitting its first *complete* tool call. Primary metric.

| Timestamp | Source |
|---|---|
| `t_send` | `time.monotonic()` immediately before the HTTP request |
| `t_first_token` | first stream event of any kind (context only) |
| `t_tool_start` | stream event opening the tool-call block (Anthropic: `content_block_start`, type `tool_use`) |
| `t_tool_complete` | the same block's closing event, arguments fully parsed. **TTFTC = `t_tool_complete` - `t_send`** |

Rules:
- **Always stream.** Non-streaming cannot separate the decision time from the time to write out arguments.
- **Client clock only.** Provider streams carry no per-event server timestamps (to my knowledge), and connectors do not report when they received a call. Verify the stream event names against the SDK during implementation; do not build a separate probe.
- **Abort the request as soon as the first tool call is complete.** Close the HTTP stream on the first `content_block_stop` of a `tool_use` block, then score it. Do not send a tool result back and do not execute the tool. Details:
  - Arguments are only complete (and checkable) at that `content_block_stop`, so that is the earliest safe abort point. Aborting earlier, at `content_block_start`, would give the tool name but no parameters.
  - If the model emits several parallel tool calls, only the first block counts; abort after it.
  - Use standard buffered tool streaming (do not set `eager_input_streaming`). The API then validates each parameter before streaming it, so the scored arguments are always valid JSON. Fine-grained streaming would deliver parameter fragments sooner but may give partial or invalid JSON; it is a different experiment.
  - **What aborting saves:** only output tokens after the abort (the rest of the reply). Input tokens are still billed in full, because the whole prompt is processed before the first output token. Thinking and the tool call itself were already generated. Whether the API stops generating and billing at the exact moment of disconnect is not stated in the docs I read; **verify in the pilot** by comparing the Console usage for a few aborted trials against the same prompts left to finish.
  - **What aborting loses:** the final `message_delta` event (output token count, `thinking_tokens`, `stop_reason`). The input usage in `message_start` is still received, so the cache assertion still works. Thinking and output cost are estimated from the events received; exact spend comes from the Console usage export.
  - TTFTC is measured before the abort, so aborting does not change the metric. Make `--no-abort` a flag so the pilot can check that.
- Record `t_first_token` and `t_tool_start` too. `t_tool_start` is the moment the model has picked a tool (the name is known; "hit a connector"); `t_tool_complete` is when the parameters are done. The gap between them is argument-writing time.

## 2. Models

Exactly these five (verified active on https://platform.claude.com/docs/en/about-claude/model-deprecations, 2026-10-16). All support `low`, `medium`, `high` effort.

| Model | API ID | Default effort | Thinking |
|---|---|---|---|
| Fable 5.1 | `claude-fable-5-1` | `high` | adaptive, always on |
| Mythos 5.1 | `claude-mythos-5-1` | `high` | adaptive, always on. **NOT AVAILABLE to this account (checked 2026-10-16)** |
| Opus 5.5 | `claude-opus-5-5` | `medium` | adaptive, always on |
| Sonnet 5.5 | `claude-sonnet-5-5` | `high` | adaptive |
| Haiku 5.5 | `claude-haiku-5-5` | `medium` | adaptive |

- **Mythos 5.1 is out of scope.** The API returns 404 for this key; the docs say it is only for organizations verified through Anthropic's verification programs (same specs and $10/$50 pricing as Fable 5.1). Phase 1 runs four models.
- Never send `temperature`, `top_p` or `top_k` (400 on recent models).
- Standard service tier only.

## 3. Effort (the one tested dimension besides tier)

Levels tested: **`low`, `medium`, `high`**. `xhigh` and `max` are not tested.

Source: https://platform.claude.com/docs/en/build-with-claude/effort and .../thinking-steering-and-cost (read 2026-10-16).

- `output_config.effort` is soft guidance on how many tokens the model spends on *all* output: thinking, text around a tool call, and the tool call's arguments. Lower effort means fewer, terser calls; it is not a guaranteed token count.
- Thinking is adaptive. At `low` the model skips thinking for simple tasks; at `medium` it may skip for simple queries; at `high` it thinks on most requests that benefit. In a tool loop the first request after new user input carries most of the reasoning, and this benchmark times exactly that request.
- **Defaults differ across models** (`high` for most, `medium` for Opus 5.5 and Haiku 5.5). Always send `effort` explicitly; never rely on the default. **`medium` is the baseline.**
- Thinking cannot be turned off on Fable 5.1 or Opus 5.5. Do not try to disable it anywhere; thinking time is part of what is measured.
- `max_tokens` counts thinking. Use 16k so the model is never truncated mid-thought. If the stream ends (or the max_tokens limit is reached) without a complete tool call, record a failed trial, not a latency. With the early abort `stop_reason` is only seen on trials that finish on their own.
- Record whether a thinking block appeared and how long it ran (time from first thinking event to its `content_block_stop`). "Did it think?" explains most latency differences. The exact `thinking_tokens` count is only in the final `message_delta`, which the early abort skips (section 1); the pilot's `--no-abort` trials can supply it.
- Do not add thinking-steering text ("think hard", "answer directly") to any prompt; effort is the only thinking control.

Hypotheses to confirm or refute in the analysis:
- TTFTC rises with effort, and the gap is larger on vague and ambiguous prompts than on obvious ones.
- Higher effort improves parameter correctness on vague prompts more than on obvious ones.
- Lower effort may skip steps or give terser calls that miss parameters (the docs warn Haiku 5.5 at `low` is more likely to skip or stop early).

## 4. System prompt (one prompt, all models, never edited between runs)

> You are an assistant with access to tools. Complete the task and return the results. Do not reply without having done the task. Do not ask for clarification; choose the most reasonable interpretation and act.

- This is the only system prompt. There is no neutral variant.
- Results depend on this wording; state the exact text next to any published result, and treat a different wording as a different experiment.

## 5. Connectors, tools and prompts

**Five connectors:** four distinct (Cloudflare, GitHub, Slack, Notion) and one adjacent (Linear) whose tools overlap with the others on purpose (issues and comments overlap GitHub; docs and projects overlap Notion), so some prompts have more than one reasonable connector. Swap Linear for Jira in config if preferred.

**Tools are definitions only.** The five connector files are in `connectors/` (92 tools, about 31k tokens by a rough count; provenance and fidelity per connector in `connectors/README.md`). GitHub, Slack and Notion are real schemas; Linear parameters are partly reconstructed; Cloudflare is hand-built because the official server is only 3 Code Mode tools. Tools are exposed as `mcp__<connector>__<tool>`. There are no running servers: the tool call is never executed, only checked against the answer key in `TEST_PROMPTS.md`.

**Notes carry the parameters.** Each user prompt includes a short notes block with the identifiers the call needs (GitHub repo/owner or issue number, Slack channel ID and user ID, Cloudflare zone ID, Notion page ID, Linear team ID, and so on). The model must extract the right IDs from the notes and use them as tool parameters; the IDs are not guessable. Each notes block also holds similar-looking distractors (a second channel ID, a second repo, an ID for a different connector) so extraction is a real test.

**Prompts: 5 per tier, 15 total**, each with a unique answer key:

| Tier | Wording | Example (task line) | Answer key |
|---|---|---|---|
| **Obvious** | Names the action and the service | "Post 'deploy done' in the releases Slack channel" | One tool, every param fixed |
| **Vague** | Same single answer, but indirect; never names the service or tool | "Let the release channel know we shipped" | One tool, every param fixed |
| **Ambiguous** | Fits two or more connectors, mostly involving Linear | "Create a ticket for the login bug" | An acceptable set: each acceptable tool has its own full param key |

Rules:
- Every ambiguous prompt needs at least one clearly wrong tool, otherwise it tests nothing.
- Vague wording must still have a defensible single answer.
- The answer key is reviewed and frozen **before** any model run. Never change it after seeing results.

## 6. Assertions: every parameter is checked and shown green or red

Each trial produces a **parameter-level result grid**, not a single pass/fail:

- First row: `tool name`, green if it is the expected tool (for ambiguous prompts, any tool in the acceptable set), red otherwise.
- Then one row per parameter in the answer key of the tool the model chose: expected value, actual value, green if it matches (exact, regex or any-of matcher, defined in the key), red if wrong.
- **A missing required parameter is red. A parameter the model emitted that is not in the key and not in that prompt's allowed-optional list is red.**
- If the tool name is wrong, show it red and still show the grid against the closest key so the wrong parameters are visible.
- A trial **passes** only if the tool name and every parameter are green. Report both the trial pass rate and the per-parameter green rate (which parameter types fail most: IDs, strings, enums).
- Outcomes that are not tool calls are recorded and shown red: `clarification` (text ending in a question), `other_text`, `error` (429, 5xx, timeout), `max_tokens` stop. Under the system prompt above a clarification is a miss, but keep the count; it is itself a finding.
- **TTFTC is measured until ANY tool in ANY connector is called, right or wrong** (column `any`: every trial that produced a tool call). The column `pass` (only fully green trials) and the pass rate always sit next to it, so a fast wrong call cannot win unnoticed. `chosen` is the earlier moment the tool name was known.

Display: colored terminal output while running, plus an HTML report generated from the JSONL (one grid per trial, rows = parameters, green / red cells, trial header with model, effort, tier and TTFTC). No other presentation work.

## 7. Matrix

Cells per model: 3 efforts (`low`, `medium`, `high`) x 3 tiers = **9**. Each cell runs the tier's 5 prompts, `--reps` times each (default **1**; raise it if the pilot shows noisy latency).

| Model | Cells | Trials at reps=1 | Trials at reps=5 |
|---|---|---|---|
| Fable 5.1 | 9 | 45 | 225 |
| Mythos 5.1 | - | - | not available |
| Opus 5.5 | 9 | 45 | 225 |
| Sonnet 5.5 | 9 | 45 | 225 |
| Haiku 5.5 | 9 | 45 | 225 |

Total: 225 trials at reps=1; 1,125 at reps=5. With n=5 per cell report **median, min and max**, not percentiles.

Controls (each affects measured speed):
- **No prompt caching.** Never send `cache_control` (Anthropic caching is opt-in). Every trial is a full uncached first call, the realistic first turn of a new session. Caches are isolated per organization and per workspace, so other people's prompts cannot produce a hit. On every response assert `usage.cache_creation_input_tokens == 0` and `usage.cache_read_input_tokens == 0`; a nonzero value invalidates the trial.
- **Order:** interleave models, efforts and tiers in random order (seeded, seed recorded) so time-of-day drift does not line up with one model.
- **Connection warm-up:** one discarded call per model before measuring, so the first measured call does not pay for opening the TLS connection.
- **Same machine, same region, same window.** Record region, local time and SDK version in the results.
- **`tool_choice` auto only.** Do not force a tool.
- **Timeout** per request; a timeout is a recorded failure. Do not silently retry anything.

## 8. Budget: $200 hard limit

- Early abort (section 1) trims output tokens only; input cost is unchanged. Per-call cost is computed from the exact input usage in `message_start` plus an estimate of the output received, so treat the running total as approximate and reconcile it against the Console usage page after the pilot.
- Measured (2026-10-16): the 92 tools are about 45,900 input tokens, not 31k. Uncached input per call: Fable 5.1 $0.46, Opus 5.5 $0.18, Sonnet 5.5 $0.09, Haiku 5.5 $0.005. Fable output measured on 6 trials: 97-588 tokens, 0-137 thinking, $0.005-0.03, so a Fable trial is about $0.47-0.49 and one pass over the 45-trial matrix (reps=1) is about $22. Opus about $8.6, Sonnet about $4.4, Haiku about $0.2 per pass (Opus/Sonnet output not yet measured). All four models together are about $35 per rep, so reps=5 is about $176.
- **Budget guard:** compute each call's cost from `usage` and the price table, keep a running total on disk, print it after every cell, and abort before it passes $150. `--dry-run` prints the projected cost per cell from token counts without calling the API.
- Run order: Haiku, Sonnet, Opus, then Fable and Mythos last.

## 9. Removed on purpose: does not change tool-call speed or call correctness

These were in earlier drafts and are not part of phase 1:
- Running mock tool servers, server receipt time and an "L3 received" check. The call is never executed; the stream's end of tool call is the metric, and the answer key is checked directly.
- Provider-hosted remote MCP mode (adds tool-list fetching and a public URL; measures something else).
- The real-connector smoke test.
- The `neutral` prompt variant, the `persist`-vs-`neutral` comparison and clarification-rate-shift analysis.
- Warm-cache and cold-cache conditions.
- `xhigh` and `max` effort, and the thinking-off lower-bound condition.
- Models other than the five above; OpenAI, xAI and open-source providers; the provider-adapter abstraction for them.
- p95 and percentile reporting (n is too small).
- Cost-per-call columns in summaries (cost is tracked only for the budget guard).
- The startup cross-check of the model list against `GET /v1/models`.
- A separate "provider timestamps" probe task.
- Daytona is optional: it only gives a clean, stable-region machine with one clock. A laptop is fine for a first pass; if used, a medium container sandbox (2 vCPU / 4 GiB, about $0.17/hour, billed per second, $200 free credits separate from Anthropic credits) is enough.

## 10. Output

- One JSONL row per trial: model, effort, tier, prompt ID, rep, seed, outcome, `t_first_token` / `t_tool_start` / `t_tool_complete` as offsets from `t_send`, TTFTC, tool name, the full parameter grid (expected, actual, green or red), thinking duration, cache usage fields (from `message_start`), whether the trial was aborted early, request ID, token counts, cost, error.
- Summary per (model, effort, tier): n, outcome counts, trial pass rate, per-parameter green rate, median / min / max TTFTC over passing trials.
- Keep raw JSONL so summaries and the HTML report regenerate without new API calls.

## 11. Build order

1. Snapshot the five connectors' tool lists; count tokens for the full tool set.
2. Write the 15 prompts with notes blocks, distractors and answer keys; review and freeze the keys.
3. Build the streaming call that records the timestamps and stops after the first tool call.
4. Build the parameter-level scorer, the budget guard, the JSONL writer and the HTML report.
5. Pilot: Haiku and Sonnet, all 9 cells, reps=1. Check that keys are sane, the grid renders, latency variance and spend. Also run a handful of prompts with `--no-abort` and compare TTFTC (should match) and Console-reported usage (shows what the abort actually saves).
6. Full run in the order in section 8; decide `--reps` from the pilot.

## 12. Implementation (built)

```
ttftc/connectors.py   load connectors/*.json as mcp__<connector>__<tool>
ttftc/scoring.py      per-parameter green/red grid against cases.json
ttftc/runner.py       streaming call, timestamps, early abort, budget guard, JSONL
ttftc/report.py       terminal grid, summary stats, HTML report
ttftc/__main__.py     python -m ttftc run | report
cases.json            15 prompts with notes and answer keys (see TEST_PROMPTS.md)
daytona_run.py        runs the same CLI inside a Daytona sandbox and downloads results
tests/                18 tests, no network (includes the real SDK against a local SSE server)
```

```
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
export ANTHROPIC_API_KEY=...
.venv/bin/python -m ttftc run --models haiku --efforts low,medium,high --dry-run      # projected input cost, free
.venv/bin/python -m ttftc run --models haiku --efforts low,medium,high --reps 1        # local run
.venv/bin/python -m ttftc run --models haiku --cases O1,V1 --no-abort --reps 1         # abort vs no-abort check
DAYTONA_API_KEY=... .venv/bin/python daytona_run.py --models haiku --efforts low,medium,high
.venv/bin/python -m ttftc report results/<run>.jsonl                                   # regenerate the HTML
```

Run models in the order of section 8 (cheap first) by invoking separate runs; within a run, trials are shuffled with `--seed`. Mythos price is assumed equal to Fable's in the budget guard.

## 13. Episodes: time to first CORRECT tool call (TTFCTC)

Additive mode (`--episodes`, code in `ttftc/episode.py`; the single-call benchmark above is unchanged).
Same prompt, tools, system prompt, efforts and scoring. After a call that is not fully green the model gets a mock tool result and keeps going, up to `--max-turns` (default 5).

- **TTFCTC**: seconds from the first request being sent until a tool call completes with every check green. Mock execution and pacing waits are excluded from the clock. The request is aborted at that call, as in section 1.
- **calls_before_correct**: red calls before the first green one (parallel calls in one message are counted individually, in stream order). `turns_to_correct` is the request count.
- **wrong_writes**: red calls to tools that change something (35 of the 92 tools; the rest are read-only, by name pattern, checked in `tests/test_episode.py`). The mock accepts every write, so a wrong write is not undone.
- **outcome**: `solved`, `gave_up` (answered in text), `clarification` (text ending in `?`), `max_turns`, `max_tokens`, `error`. Unsolved episodes are counted, never timed.
- **Mock results** carry no hints: writes return `{"ok": true}`, lookups return an empty or shape-only object (`FIXTURES` in `episode.py`, filled from the call's own input).
- **Cost**: every turn re-sends the whole tool surface (about 46-48k tokens, no caching), so cost is roughly turns x the section 8 per-trial input cost. Non-green turns are read to the end, so their output tokens are billed.
- A first-turn aborted trial from the single-call mode cannot be continued (the thinking signature is lost), so episodes are re-run from scratch.
- Run: `.venv/bin/python -m ttftc run --episodes --models haiku --efforts low,medium,high --reps 1`. `--resume` and `daytona_run.py` work unchanged.
