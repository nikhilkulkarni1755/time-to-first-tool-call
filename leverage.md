# leverage.md: which connector changes buy the most speed?

Status: DRAFT 2, design only (decisions from review folded in: section 13). Nothing in here is built or run yet. We refine this file until the plan is tight, then build.
Builds on `PROMPT.md` (single-call benchmark, section 13 for episodes) and `TEST_PROMPTS.md` / `cases.json` (the 15 prompts).

## 1. Why this exists

2027.dev ranks services (sandboxes, email, search, ...) on Time, Cost, Errors and Interruptions. It does not rank models.
So the question here is not "which model is fastest" but **"what can a service change about its tools so that any model, Haiku or Fable, reaches a correct tool call sooner?"**

Every earlier number in this repo was model behaviour on a fixed connector surface. This experiment holds the model and the prompts fixed and moves the connector.

**Thesis to test:** a few connector design choices (not one) account for most of the latency and wasted calls, and the best bundle of them beats a realistic "bad" connector by a blatant margin on both Haiku (reflex model) and Opus (near-frontier, multi-step; replaces Sonnet as the second model).

## 2. What we already know (from the 360 single-call trials + 45 Haiku episodes)

- First byte is a 0.7-1.2s floor whatever the model. Thinking is the swing cost (30-43% of the time on ambiguous prompts). Long arguments are slow: 600+ char arguments took 3.8s to write against 0.5s for under 100 chars.
- Tool surface is 92 tools, about 46k tokens (2.75 characters per token, measured): Notion 38%, Slack 27%, GitHub 18%, Cloudflare 9%, Linear 8%.
- Wrong first calls are mostly lookups (`notion-fetch` 30, `github.issue_read` 30 of 79). One has a 1,900-char description, the other 17 chars. Both attract lookups, so length alone is not the lever. Untested: it is the *cue* about when not to call.
- GitHub's vague prompt (V2) was 33% green first call, 62% lookup-first. Only one prompt, so a lead, not a finding.
- Effort changes whether a model thinks, not how long it thinks, and it raises lookup-first rates on Haiku and Opus.

## 3. The design in one paragraph

The **subject** is the GitHub + Linear pair varied together (they overlap, which is the case competitors will attack). Slack, Notion and Cloudflare stay at baseline as realistic distractors. Notion as a subject is a later round. Build a **bogeyman**: a realistic worst-case version of it. Then define **dials**, each a design dimension with 3-5 levels from "bad" to "best". Run the **same 15 prompts** against every variant. Report the latency/accuracy change of each dial from the bogeyman, then of the best bundle, then **leave-one-out ablations** of the best bundle so we can say *which* change carried the gain. No single trick is allowed to be the whole story: each dial gets several iterations of wording, and the final claim must survive ablation.

## 4. The bogeyman (the deliberately bad connector)

Must be **realistic, not a strawman**: every flaw is a pattern seen in real MCP servers (a 2026 study found 97.1% of sampled MCP tool descriptions had at least one quality smell, official servers no better than community ones: arXiv 2602.14878).

| Dial | Bogeyman setting |
|---|---|
| D1 Surface size | 60+ tools for the subject connector (baseline tools plus plausible near-duplicates), everything loaded up front |
| D2 Descriptions | 1.5-2k chars of generic prose per tool, purpose buried, no "when not to use" |
| D3 Identifiers | opaque IDs only (numeric/node IDs), no names accepted |
| D4 Naming | generic verbs (`get`, `list`, `write`), no service or intent in the name |
| D5 Granularity | low-level multi-method tools (`issue_write` + `method`), many calls to finish one task |
| D6 Schema | 15-20 optional params, nested objects, long free-text defaults invited |
| D7 Instructions | none |
| D8 Results/errors | huge verbose results, opaque error codes |
| D9 Overlap | GitHub and Linear tools worded identically ("List issues") |

Expectation: the bogeyman is slower on first byte (more input), slower on arguments (bigger schema), more lookup-first, and more wrong-service picks.

## 5. The dials (bad to best), what each should improve, and our prediction

Each dial changes ONE thing against baseline. "Improves" names the dimension we expect it to move; a dial that moves a different one, or none, is a finding too.

| Dial | Levels (bad -> best) | Should improve | Prediction (to check) |
|---|---|---|---|
| D1 Surface size | 60+ tools / baseline 20 / trimmed 8 core / 4 core | time to first byte, input cost | linear-ish TTFB gain; obvious prompts benefit only here |
| D1b Deferred loading | all loaded / tool-search with defer_loading | time to first byte, cost | helps large surfaces, can hurt small ones (extra search step) |
| D2 Descriptions | bloated prose / vague 17-char / baseline / short directive / directive + one example | lookup-first rate, thinking time | directive beats both extremes; length is not the lever (arXiv: richer descriptions +5.85pt success median but +67% steps and 16.7% regressions, so verbosity costs steps) |
| D3 Identifiers | IDs only / name accepted or ID / `owner/repo` and `#n` shorthand / fuzzy resolve with "did you mean" error | lookup-first rate, wrong-ID rate | removes lookups whose only purpose is resolving a name |
| D4 Naming | generic / service-prefixed / intent names (`close_issue`) / prefix vs suffix variants | wrong-tool rate, selection time | Anthropic reports prefix vs suffix has non-trivial, model-dependent effects |
| D5 Granularity | low-level methods / baseline / one tool per intent / consolidated task tool | calls before correct, turns | fewer turns for multi-step intents |
| D6 Schema | 20 optional params / baseline / required-only / required-only with defaults | argument-writing time, extra-param reds | shorter arguments, fewer harmless-extra reds |
| D7 Server instructions | none / one line ("IDs in the request are final, call the write tool directly") / per-intent routing hints | lookup-first rate | cheapest dial, but changes the system prompt: separate arm, clearly labelled |
| D8 Results and errors | verbose + opaque error / baseline / concise result + corrective error ("owner missing, use owner/repo") | turns after a wrong call, TTFCTC tail | only visible in episodes, not single-call |
| D9 Overlap (GitHub vs Linear) | identical wording / baseline / scoped descriptions ("repo bugs" vs "planning") / scoped + namespaced names | wrong-service rate on A1-A4 | the case competitors will try; needs its own readout |

**Plus two bracket arms:** `minimal` (descriptions stripped, 3-4 tools) to show whether "just make it tiny" is faster *and* where correctness collapses, and `best` (best level of each dial). We want to show a trade-off curve, not a single winner.

### Anti-"just swap IDs for names" rule
A dial is only reported with at least three distinct iterations (for D3: ID-only, name-or-ID, shorthand, fuzzy-resolve), and the headline is never one variant: it is the **leave-one-out table** of the best bundle. If removing the name change from the best bundle loses little, names are not the lever, whatever the single-dial result says.

## 6. Keeping the experiment consistent

1. **One frozen base.** Same 15 prompts, same keys (v3), same system prompt, same mocks, same SDK settings (streaming, explicit effort, no caching, abort at first correct call). Only the connector files differ.
2. **One change per arm.** A variant is a diff from baseline in one dial (or a declared bundle). Variants live as files, are hashed, and every result row records `variant_id` and `variant_hash`.
3. **Paired and interleaved.** Every arm runs the identical (case, model, effort, rep) list; the global order is shuffled across arms with a recorded seed so API load and time of day hit all arms equally.
4. **Drift control.** The baseline arm is run at the start, middle and end. If its median moves more than the noise floor (obvious prompts about +/-0.1s on Haiku), the batch is flagged.
5. **Keys follow the variant.** A variant that renames a tool or parameter ships a *semantic* key mapped to the canonical answer, and a test proves every variant key is solvable and schema-valid (same checks as today's `cases.json` tests). Scoring is never loosened to make a variant look good.
6. **Mocks are identical** across arms except D8, where the mock is the variable.
7. **Pre-registered decision rules** (section 8) are written before any run.
8. **Two behavioural archetypes.** Haiku acts at once; Sonnet takes multiple steps, our proxy for frontier behaviour. A change that helps one and hurts the other is reported as such, plus *model spread* (slowest model minus fastest).

## 7. Vague vs specific prompts: keep or drop?

You asked for the pros and cons of removing the tiers. Recommendation: **keep all three tiers.**

| | Keep obvious + vague + ambiguous | Drop to specific-only |
|---|---|---|
| Signal | obvious is the **negative control**: a dial that only changes latency (D1, D6) moves it, a dial that changes behaviour (D2, D3, D7) should not | cleanest pure-latency read |
| Coverage | shows lookup-first, wrong-service and give-up behaviour, which is where the TTFCTC gap lives | loses all of it; TTFCTC would equal TTFTC and the headline metric disappears |
| Overlap tests | A1-A4 (GitHub vs Linear) are the case competitors will attack | cannot test D9 at all |
| Cost | 15 prompts per arm | 5 prompts per arm, a third of the cost |
| Noise | vague/ambiguous cells are noisy at few reps | stable |
| Risk | one prompt can dominate a connector's score (GitHub vague = V2 only) | n/a |

Mitigation for the noise: more reps on Haiku (cheap), fewer on Sonnet; report tiers separately, never one blended number.

## 8. Metrics and decision rules

Primary: **TTFCTC** (episode time to first fully green call). Secondary: TTFTC, first-call-green %, lookup-first %, calls before correct, solved %, wrong writes, input tokens per request, argument characters, cost per episode, effort sensitivity (high minus low effort), model spread.

Latency is decomposed per arm (time to first byte / thinking / argument writing / extra turns) so we can say *why* a dial worked.

A dial **counts as leverage** if, on Haiku and on Sonnet separately:
- median TTFCTC (vague + ambiguous) improves by at least 25% and at least 0.5s versus bogeyman, with the paired bootstrap interval excluding zero; and
- solved % does not drop by more than 5 points.

"Blatant" = the best bundle is at least 40% faster than the bogeyman on both models. Anything smaller is reported as "no clear leverage".

## 9. Staged plan and budget

Credit left is roughly $132 (estimated; reconcile with the Console before stage 2). Measured per-request input cost at the 46k-token baseline: Haiku $0.005, Opus $0.18. Episodes cost about 1.4x a single call. Surface sizes (estimated from characters at 2.75 chars/token): baseline 46k, bogeyman about 77k (all 97 real GitHub tools), best about 37k.

| Stage | What | Models / efforts | Rough cost |
|---|---|---|---|
| 0 | Build dials, variants, per-variant keys, tests; verify deferred loading | none | $0 |
| 1 Screen | 5 anchor arms + one-dial-at-a-time (L0 and L3) + repair-one + leave-one-out, about 41 arms x 15 prompts x 3 efforts x 2 reps | Haiku, low/medium/high | about $30 |
| 2 Anchors | baseline, bogeyman, best, nudge (about $11.6 + $19.5 + $9.5 + $11) x 15 prompts x 3 efforts x 1 rep | Opus, low/medium/high | about $52 |
| 3 Ablate | the 3 leave-one-out arms that stage 1 ranks highest, about $10 each | Opus, low/medium/high | about $30 |
| 4 Optional | minimal arm, about $9 | Opus | about $9 |

Total about $120 of about $132: tight. Order of priority if credit runs short: baseline, bogeyman, best, nudge, leave-one-out, minimal. Output tokens are not in these estimates; a per-stage budget guard stops each stage. Opus has 1 rep per cell, so it can show blatant differences only; subtle ones need more credit.

## 10. Effort awareness

We found no mechanism in the MCP spec for a server to learn the calling model or its effort: a tool call carries the client's name and capabilities, and model preferences exist only for server-initiated sampling. So we do not test "effort-aware connectors". We test **effort-robust** connectors: the first plausible call should be right whether the model reflexes (Haiku) or thinks (Fable), measured by effort sensitivity and model spread.

## 11. Decisions taken in review (was: open questions)

1. Subject = GitHub + Linear together; Slack, Notion, Cloudflare fixed. (decided)
2. D7 server instructions = its own clearly labelled arm. (decided)
3. Everything real: the bogeyman's extra GitHub tools are the other 76 tools of the real 96-tool `github-mcp-server` README. Linear stays at 20 (reconstructed, medium fidelity), so Linear arms change wording and structure, not tool count. (decided)
4. Second model = Opus (claude-opus-5-5), all three efforts, 1 rep. Haiku all three efforts, 2 reps. (decided)
5. Still open: see section 13.

## 12. Sources behind the predictions

- 2027.dev (home and /arena): categories, four metrics, harness x model picker.
- arXiv 2602.14878 (MCP tool descriptions are smelly): 97.1% with a smell; augmented descriptions +5.85pt median success, +67.46% median steps, regressions in 16.67% of cases; no single golden component set.
- Anthropic, "Writing effective tools for agents": namespacing, prefix vs suffix has model-dependent effects, readable identifiers beat opaque ones, consolidate tools by intent, concise vs detailed responses, corrective errors.
- Anthropic, "Introducing advanced tool use": a five-server, 58-tool setup is about 55k tokens; Tool Search with `defer_loading` reportedly about 8.7k.
- Secondhand (not verified by us): RAG-MCP selection accuracy 43% to under 14% as tool count grows.

## 13. Draft 2: how the dials are pressed, arms, and a worked sample

### 13.1 Four levels per dial, one scale

Each dial has levels **L0 / L1 / L2 / L3**:

| Level | Meaning |
|---|---|
| L0 | worse than shipped, in a way real servers are |
| L1 | **as shipped** today (this is the baseline; for GitHub the shipped descriptions are already terse, e.g. "Get issue details") |
| L2 | clearly better, conservative edit |
| L3 | best we can find |

Rule: whatever the level, every tool that any accepted answer key uses must still exist (GitHub `issue_write`, `add_issue_comment`, `issue_read`; Linear `save_issue`, `save_comment`, `get_issue`, `list_issues`), so each prompt stays solvable in every arm.

| Dial | L0 | L1 (shipped) | L2 | L3 |
|---|---|---|---|---|
| D1 Surface | GitHub all 97 real tools + Linear 20 | GitHub 20 + Linear 20 | 10 + 10 core tools | 5 + 5 issue/comment tools |
| D1b Deferred | n/a (L0 = L1) | all loaded | tool search, 3 tools kept loaded | tool search, 0 kept loaded (verify API first) |
| D2 Descriptions | bloated marketing prose, 1.5k chars | terse ("Get issue details") | purpose + what it returns | purpose + "do not call this before a write when owner/repo/number are given" |
| D3 Identifiers | opaque node IDs only | owner + repo + number | `owner/repo` single param, `#n` accepted | also resolves names, error says "did you mean billing-api?" |
| D4 Naming | generic (`get`, `write`) | `issue_read`, `issue_write` | service-prefixed (`github_issue_read`) | intent names (`github_close_issue`, `github_comment_on_issue`) |
| D5 Granularity | multi-method (`issue_write method=...`) + separate calls for every step | shipped (multi-method) | split create / update | one tool per intent, incl. consolidated "update issue" |
| D6 Schema | 17 params + extras, nested | 17 params (issue_write) | required + 4 common optional | required only (defaults server-side) |
| D7 Instructions | none | none | one line | per-intent routing hints (GitHub vs Linear) |
| D8 Results/errors | 3k-token raw issue JSON, opaque error code | shipped-style mock (current empty/shape) | concise result | concise + corrective error with next step |
| D9 Overlap | GitHub and Linear worded identically | shipped wording | scoped descriptions | scoped + namespaced names |

### 13.2 Which arms (one dial vs all dials)

Both, on purpose, because dials interact (fewer tools make the GitHub/Linear confusion smaller, so effects do not add up):

| Family | Arm | Dial vector | Answers |
|---|---|---|---|
| Anchors | baseline | all L1 | the reference |
| | bogeyman | all L0 | the bad case |
| | best | all L3 | the ceiling |
| | nudge | all L2 | does a little change everywhere compound into a drastic result? |
| | minimal | stripped to the bare minimum | where does tiny stop being correct |
| Add-one-bad | `bad-Dn` (9 arms) | baseline, with dial n at L0 | which flaw hurts a shipped connector most |
| Fix-one | `fix-Dn` (9 arms) | **bogeyman, with dial n at L3** | which single repair helps the bad connector most |
| Improve-one | `up-Dn` (9 arms) | baseline, with dial n at L3 | which single improvement helps a shipped connector most |
| Leave-one-out | `loo-Dn` (9 arms) | best, with dial n back at L1 | which dial the best bundle depends on |

Stage 1 (Haiku) runs all 41 arms: 5 anchors + 4 families x 9 dials. D1b (deferred loading) counts as a tenth dial only if the API check passes. Opus runs the anchors plus the three most important leave-one-out arms.

Reading the result: `fix-Dn` and `loo-Dn` together give each dial's value in a bad connector and its necessity in a good one. A dial that wins `up-Dn` but not `loo-Dn` is a nice-to-have; one that wins both is the leverage.

### 13.3 Worked sample: prompt V2 ("Close out the bug about invoices rounding wrong. It's sorted.")

Notes give owner `acme-labs`, repo `billing-api`, issue 391. The correct answer is one `github.issue_write` close (today's keys). All numbers below are **predictions to check, not measurements**; the baseline row is what we measured for Haiku/Opus V2 (33% green first call, 62% lookup-first across all models).

What the model is shown for the GitHub issue tools in three arms:

```
bogeyman (all L0)                     baseline (L1, as shipped)                 best (all L3)
-------------------------------      ---------------------------------------   --------------------------------------------
97 GitHub tools + 20 Linear           20 GitHub + 20 Linear                    5 GitHub + 5 Linear
write(id: "I_kwDOA...", ...17+)      issue_write(method, owner, repo,         github_close_issue(repo: "acme-labs/
get(id)  "Retrieves comprehensive     issue_number, state, ... 17 params)        billing-api", number: 391)
 information regarding a specified   issue_read(method, owner, repo,           github_read_issue(...): "Read one issue.
 issue ... leveraging REST and        issue_number)                              Do NOT call before closing/commenting
 GraphQL ... (1.5k chars)             "Get issue details"                         when the number is given."
list_issues: "List issues"  ==       list_issues: "List issues"                github: "repo bugs and PRs"
linear list_issues: "List issues"    linear list_issues: "List issues in the   linear: "planning tickets for the
                                      user's Linear workspace..."                Platform team"
```

Predicted behaviour of the episode (Haiku medium):

| Arm | Expected first call | Calls before correct | Prediction |
|---|---|---|---|
| bogeyman | `get(...)` lookup, maybe a Linear lookup | 2-3 | slowest first byte (about 77k tokens in), long args, wrong-service risk |
| baseline | `issue_read` (62% of the time) | 0-1 | measured: TTFTC about 2.1s, 33% first-call green |
| best | `github_close_issue(repo, 391)` | 0 | smallest input, short args, no lookup |

### 13.4 Worked sample: prompt A1 ("Create a ticket for the login bug.") — the overlap case

Both a GitHub issue in `auth-service` and a Linear issue in Platform are accepted. Bogeyman wording makes the two `list_issues`/`write` tools indistinguishable, so we expect searches in both, then a coin-flip write. At D9 L3 the descriptions say what each is for, so the model should pick one and write once. The readout here is **wrong-service rate and calls before correct**, not just time.

### 13.5 The pipeline at the end (draft, no code yet)

```
variants/
  dials.json          the 9 dials x 4 levels: what each level changes
  arms.json           each arm = {dial: level, ...} + id (e.g. "fix-D2", "loo-D3")
  github_full.json    the real 97 tools parsed from the official README (source of D1-L0)
  text/               description wording per dial level (versioned, reviewed)
connectors/           unchanged baseline (L1 for every dial)
    |
    v   build_arm(arm)  =  baseline connectors  +  each dial's transform  (pure, deterministic, hashed)
tools for the request  +  mock results (D8)  +  system prompt (+ instructions block for D7)
    |
    v   episode runner (exists)  ->  rows tagged arm_id, arm_hash, dial vector
    |
    v   scorer: canonical key + per-arm adapter (renamed tools/params), proven solvable by tests
report: scorecard per arm and model:
  TTFCTC (median, p90)  | delta vs bogeyman | delta vs baseline | solved %
  lookup-first % | wrong-service % | calls before correct | input tokens | arg chars | cost
  plus: dial-effect chart (fix-Dn, up-Dn, loo-Dn side by side) and latency split (first byte / thinking / args / extra turns)
```

### 13.6 Still unclear (defaults I will use unless told otherwise)

1. Baseline = "as shipped" (already terse for GitHub). Claims are "vs shipped" and "vs bogeyman", never "vs good".
2. All arms run as episodes; TTFCTC is primary. Single-call TTFTC is recorded from the same rows.
3. Prompts and Notes are untouched. Variants only change what the tools look like; the Notes never gain names or hints.
4. D8 result payloads: bogeyman = a real-shaped GitHub issue object (about 3k tokens); best = a few fields. Needs your sign-off on how real/long.
5. Deferred loading: verify availability and what "first tool call" means before building D1b.
6. Opus has 1 rep: we report effect sizes with intervals and call anything under 25% "no clear leverage".
7. Reconcile credit with the Anthropic Console before stage 2.

## 14. Build status (stage 0 done, nothing run live)

Built and tested offline (93 tests pass): `ttftc/dials.py` (9 dials x 4 levels, variant builder, answer-key mapping), `ttftc/leverage.py` (39 distinct arms, run loop, loud halts, scorecard), `variants/github_full.json` (the real GitHub tool list: 96 tools parsed from the official README, 76 beyond the shipped 20), hooks in `episode.py` (canonical mapping, per-arm mock results, HTTP status of failures).

- **Proof the keys survive every variant:** for all 39 arms and all 22 accepted branches, the correct call expressed in that variant validates against the variant's schema, maps back to the canonical call, and scores green. Answer keys are unchanged.
- **Arms:** 5 anchors (baseline, bogeyman, best, nudge, minimal) + bad / up / fix / loo for each of the 9 dials = 41, minus `bad-D5` and `bad-D7` (D5 and D7 have no level worse than shipped, so they equal baseline and are dropped automatically). D1b (deferred loading) is not built: waiting for the API check.
- **Order of dials applied:** D1 surface, D5 granularity, D6 schema, D3 identifiers, D4 naming, D9 identical wording, D2 descriptions, D9 scoping. D9 L0's identical wording is applied before D2 so the bogeyman's bloat still lands on the overlap tools (a test caught this).
- **Loud halts (autonomous run stops, then manual resume):** HTTP 401 / 402 / 403 / 404, any message mentioning credit balance, billing, spend or usage limit, payment, suspended or authentication, or five errors in a row, stops the run with exit code 3: red banner, terminal bell, `<out>.STOPPED.json`, and the exact resume command. A spent ledger (default $130, shared by every leverage run in `results/leverage/spend.json`) stops it with exit code 4. The failed episode is saved but never counted as done, and no further request is sent. Resume = rerun the printed command (`--resume` and `--out` pointing at the same file): only the missing episodes run, and the ledger carries on from where it was.
- **A key check runs before the first episode** (a free `count_tokens` call), so a dead key or empty balance fails in the first second.
- **Commands:** `python -m ttftc leverage arms` (list arms, tokens, hashes); `... leverage run --arms anchors --models haiku --dry-run` (cost projection); `... leverage run --arms all --models haiku --efforts low,medium,high --reps 2`; `... leverage report FILE`.

Offline cost projection (characters / 2.75, which overestimates prose; the real figure needs a live `count_tokens`): Haiku, all 39 arms, 3 efforts, 2 reps about $29. Opus anchors, 3 efforts, 1 rep: baseline $11.6, bogeyman about $30, best $9.3, nudge $9.6, minimal $8.8. Bogeyman is the expensive arm (about 120k tokens per request). Ledger limit $130.

Live runs are done: see the README for results.
