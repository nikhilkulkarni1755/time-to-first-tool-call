# time-to-first-tool-call

Preliminary testing for **time to first tool call (TTFTC)** and **time to first *correct* tool call (TTFCTC)** with Claude Haiku 5.5, Sonnet 5.5, Opus 5.5 and Fable 5.1.

This is early work: small samples, one run per setting, one machine. Treat the numbers as directional, not final.

## Why this matters

An agent does nothing useful until it calls a tool, so the wait before that call is the wait the user feels. Two things make it worse than it looks:

- **The first call is often wrong.** A fast wrong call (a lookup, a call to the wrong service, a malformed argument) just adds another round trip. What the user actually waits for is the first *correct* call, not the first call.
- **The model is only half the story.** The same model gets faster or slower, and more or less accurate, depending on the tools it is given: how many there are, what they are called, how they are described, what IDs they ask for. That part is controlled by the service or connector, not by the model provider or the person writing the agent.

If tool design moves latency by seconds and correctness by tens of points, services can be compared on it, and a service can fix it.

## How the experiment came about

1. **Stage 1: just time it.** Measure the seconds from sending a request to the first completed tool call (streaming, abort at the first tool block) for four models at three effort levels, over 15 prompts across five connectors (GitHub, Slack, Notion, Linear, Cloudflare; 92 tool definitions, no live servers). Prompts come in three tiers: obvious, vague and ambiguous.
2. **Stage 2: time the *correct* call.** A fast call is not a useful call. So an episode keeps going: wrong calls get a mock tool result and the model continues, up to 5 turns, until one call passes every check. TTFCTC is the time to that call. Mock execution and pacing waits are excluded from the clock. This also counts calls before the correct one, wrong writes and lookups.
3. **Stage 3: change the services, not the model.** The first two stages mostly measured models. The more interesting question is which *service-side* choices make latency and wasted calls go up or down on demand. So the "leverage" experiment (see [`leverage.md`](leverage.md)) takes GitHub and Linear and builds one deliberately bad "bogeyman" connector (a 96-tool surface from the real GitHub tool list, bloated descriptions, opaque IDs, inconsistent names, overlapping tools) and nine dials that move it from bad to best: surface size, descriptions, identifiers, naming, granularity, schema, server instructions, result and error shape, and GitHub/Linear overlap. 39 distinct arms were run. If a change to the connector moves the numbers while the model and prompts stay fixed, the connector is doing it.

## Running it locally

Needs Python 3.10+ and an Anthropic API key. Real runs cost money; every command has a budget limit and a `--dry-run`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # set in your shell only, never commit it

python -m pytest -q                 # 93 offline tests, no API calls

# Single-call TTFTC (15 prompts x 3 efforts)
python -m ttftc run --models haiku --efforts low,medium,high --dry-run
python -m ttftc run --models haiku --efforts low,medium,high --out results/my-run.jsonl

# Episodes (TTFCTC): keep going until a call is fully correct
python -m ttftc run --models haiku --episodes --max-turns 5 --out results/my-episodes.jsonl

# Leverage experiment: bogeyman, dials and arms
python -m ttftc leverage arms
python -m ttftc leverage run --arms baseline,bogeyman,best --models haiku --efforts low --dry-run
python -m ttftc leverage run --arms baseline,bogeyman,best --models haiku --efforts low --reps 1 --out results/leverage/mine.jsonl
python -m ttftc report results/leverage/mine.jsonl
```

If the API returns a billing, auth or access error (HTTP 401/402/403/404, or five errors in a row), the run stops with a loud banner, writes `<out>.STOPPED.json` and prints the exact command to resume. The leverage runs also share a spend ledger that halts at the limit.

`PROMPT.md` is the full method spec, `TEST_PROMPTS.md` the prompts and answer keys, `connectors/` the tool definitions with their provenance, and `daytona_run.py` an optional script to run from a cloud sandbox.

## Findings

All numbers come from the files in `results/`. Times are seconds on a laptop network, so compare within a table, not across.

### What drives time to first tool call

From the four-model, 360-trial single-call run (`results/final/`), 10 trials per model, effort and tier:

- **Obvious prompts are solved by everyone:** 10 of 10 correct for every model at every effort, with a median of roughly 1 to 2.4 seconds. Obvious tasks are not where models or tools differ.
- **Ambiguous prompts are where it falls apart:** only 1 to 6 of 10 were fully correct, depending on model and effort, and the slow ones were slow because of thinking. For example, Fable at high effort had a median of about 7.8 seconds on ambiguous prompts. Thinking was 30 to 43% of the time on ambiguous prompts.
- **Higher effort did not reliably buy correctness on ambiguous prompts** in this sample. It did reliably buy time.
- **There is a fixed floor:** first byte takes about 0.7 to 1.2 seconds regardless of anything else.
- **Long arguments cost time:** a call whose arguments exceeded 600 characters took about 3.8 seconds against about 0.5 seconds for short ones.
- **Models look things up first:** of 79 lookups before the real action, 60 were `notion-fetch` and GitHub `issue_read`. A lookup is a wasted turn when the answer was already in the prompt.

The single-call correctness above was scored with the first, stricter answer keys. After the episode work, the keys for four prompts were loosened to accept more than one defensible interpretation, which moved Haiku modestly. The 360-trial file was not rescored.

### How a service can cut time to first (correct) tool call

Haiku 5.5 across all 39 arms, 3 efforts, 2 reps (3,510 episodes, about 90 per arm; `results/leverage/stage1-haiku.*`):

| Arm | Solved | First-try correct | Lookup-first | Slowest 10% on vague/ambiguous | Median TTFCTC | Tokens in | $/episode |
|---|---|---|---|---|---|---|---|
| bogeyman | 84% | 58% | 32% | 6.10s | 3.09s | 109,555 | $0.020 |
| baseline (as shipped) | 86% | 68% | 28% | 7.44s | 2.19s | 45,963 | $0.008 |
| nudge (every dial a little better) | 88% | 68% | 26% | 9.12s | 2.68s | 38,592 | $0.007 |
| best (every dial at best) | 99% | 90% | 10% | 3.78s | 2.38s | 37,256 | $0.004 |
| minimal (4 key tools only) | 100% | 88% | 12% | 4.35s | 2.17s | 35,006 | $0.004 |

Opus 5.5 at medium effort, 30 episodes per arm for the first three rows and 15 for the rest, from two reps (`results/leverage/stage2-opus-merged.*`):

| Arm | Solved | First-try correct | Lookup-first | Slowest 10% on vague/ambiguous | Median TTFCTC | $/episode |
|---|---|---|---|---|---|---|
| baseline | 87% | 60% | 40% | 11.06s | 4.32s | $0.33 |
| bogeyman | 90% | 67% | 33% | 10.11s | 4.27s | $0.64 |
| best | 100% | 100% | 0% | 4.87s | 3.26s | $0.15 |
| best without the schema dial | 93% | 93% | 0% | 6.48s | 2.98s | $0.17 |
| best without server instructions | 100% | 100% | 0% | 6.83s | 3.43s | $0.15 |
| minimal | 100% | 80% | 20% | 9.18s | 4.47s | $0.17 |

What these show:

1. **Tool design moves correctness and wasted calls a lot, for both models.** Going from the shipped tools to the best design took first-try correctness from 68% to 90% on Haiku and from 60% to 100% on Opus, and removed most lookup-first calls.
2. **It cuts the slow tail.** The slowest 10% on vague and ambiguous prompts fell from 7.44s to 3.78s on Haiku and from 11.06s to 4.87s on Opus. The tail is what a user remembers.
3. **Median latency fell for Opus (4.32s to 3.26s) but not for Haiku (2.19s to 2.38s).** The Haiku result is probably a measurement effect: only solved episodes are timed, and the best design solves hard prompts that baseline fails, so the slower ones enter the median. This was not checked.
4. **A big, messy surface is a cost even when accuracy holds.** On Opus the bogeyman (109k input tokens) matched baseline on accuracy but cost about twice as much per episode. On Haiku it was the worst arm on every measure.
5. **"A little better everywhere" did nothing.** On Haiku, nudge was no better than baseline on first-try or solved rate (it was not run on Opus). The gain showed up only when the dials were at their best setting.
6. **Schema and server instructions are the strongest single levers on Haiku.** Tightening the parameter schema took Haiku from 86% to 99% solved, and removing it from the best design is the only removal that hurt (99% to 89% solved). Server instructions cut lookup-first calls by about 20 points. On Opus, removing either left results near 100%, so a stronger model needed less help.
7. **Shrinking the tool list alone is not enough.** Cutting the bogeyman from 109k to 42k tokens did not improve its first-try rate (58% to 67%). The 4-tool minimal arm matched best on Haiku but not on Opus (80% first-try).
8. **Making things worse does slow things down.** Six of the seven worse-than-shipped arms were slower than baseline on Haiku in the hard-prompt median (the identifier arm was level); only the schema one had a 95% interval that excluded zero (+41%).
9. **Haiku and Opus differ.** What Haiku needs from a connector (a strict schema) is not what Opus needs, which is a reason to test per model and per effort rather than assume one design fits all.

**Limits.** One effort (medium) for Opus, about 15 to 30 episodes per Opus arm, about 90 per Haiku arm, one prompt set of 15 prompts, mocked tool results, and no real servers. Most single-dial effects have wide intervals that include zero; the headline comparisons (best against baseline) are the ones that hold up. Sonnet and Fable were only run in the single-call stage. Deferred tool loading, which Anthropic describes as cutting a 55k-token surface to under 9k, was designed (`leverage.md`) but not run.

## How I would design the experiment next time

The connector lever worked. The next version should push it through the language the model actually reads and the way tasks are phrased, not only the structure around it.

- **Vary the description language, not just its presence.** Hold the tool set fixed and sweep description *length* (one line, one paragraph, one page) and *semantic content* (what it does, when to use it, when not to, an example call, a counter-example). This separates "more words" from "the right words".
- **Vary naming and parameter wording as language.** Verb-first against noun-first names, a service prefix against none, `issue_number` against `id`, with the same function behind each.
- **Treat prompt ambiguity as a controlled variable.** Build the same task at graded levels of ambiguity (fully specified, one missing field, two plausible services, genuinely underspecified) instead of three coarse tiers, and add cases where asking a clarifying question is the right answer. Score that separately from a wrong call.
- **Measure per model and per effort from the start,** with more than one rep, and report intervals. Use the same episodes for every arm and time the same set of solved episodes in every arm so the median is comparable.
- **Add the deferred-loading arm,** and decide up front what "first tool call" means if the search step runs server-side.
- **Run live servers for a subset** to see whether real result shapes and error messages change the picture.
- **Keep the harness honest:** same system prompt, no prompt caching, effort sent explicitly, spend limits with a loud, resumable halt.
