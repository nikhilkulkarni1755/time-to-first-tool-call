"""Multi-turn episodes: keep answering tool calls with mock results until a call scores fully green.

TTFCTC (time to first correct tool call) = seconds from the first request being sent until a tool call completes
and passes every check. Mock execution and pacing waits are excluded from the clock.
"""
import json
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from . import connectors, report, runner, scoring

READ_ONLY = re.compile(r"(^|[_-])(get|list|search|read|fetch)([_-]|$)|_read$")
# Shape-only answers: nothing here tells a model which tool or parameters to use. {x} is filled from the call's own input.
FIXTURES = {
    "github.issue_read": {"number": "{issue_number}", "state": "open", "title": "", "body": "", "comments": 0},
    "notion.notion-fetch": {"id": "{id}", "title": "", "content": ""},
    "linear.list_issues": {"issues": [], "hasNextPage": False},
    "cloudflare.get_zone": {"id": "{zone_id}", "status": "active"},
    "slack.slack_read_channel": {"messages": []},
    "linear.get_issue": {"id": "{id}", "title": "", "state": {"name": "Todo"}},
}
DEFAULT_READ = {"results": [], "total_count": 0}
DEFAULT_WRITE = {"ok": True}


def is_read_only(logical):
    return bool(READ_ONLY.search(logical.split(".", 1)[1]))


def _fill(template, values):
    if isinstance(template, str):
        return template.format_map(values) if "{" in template else template
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items()}
    return template


def mock_result(logical, tool_input):
    """JSON text returned as the tool result: the same shape for every model, no side effects."""
    if not is_read_only(logical):
        return json.dumps(DEFAULT_WRITE)
    values = {k: v for k, v in tool_input.items() if isinstance(v, (str, int))}
    return json.dumps(_fill(FIXTURES.get(logical, DEFAULT_READ), defaultdict(str, values)))


def _turn(client, model, effort, messages, tools, system, clock, on_tool, abort_on, max_tokens, timeout):
    """Stream one response. Returns (content blocks, tool_use calls, usage, stop_reason, text, aborted, error)."""
    blocks, raw, usage, stop, calls, aborted, error, status = {}, {}, {}, None, [], False, None, None
    stream = None
    try:
        stream = client.messages.create(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages,
                                        stream=True, timeout=timeout, output_config={"effort": effort})
        for ev in stream:
            if ev.type == "message_start":
                u = ev.message.usage
                usage = {k: getattr(u, k, None) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
            elif ev.type == "content_block_start":
                b = ev.content_block
                if b.type == "tool_use":
                    blocks[ev.index] = {"type": "tool_use", "id": getattr(b, "id", f"toolu_{ev.index}"), "name": b.name, "input": {}}
                    raw[ev.index] = []
                    on_tool("start", blocks[ev.index])
                elif b.type == "text":
                    blocks[ev.index] = {"type": "text", "text": ""}
                elif b.type == "thinking":
                    blocks[ev.index] = {"type": "thinking", "thinking": "", "signature": ""}
                elif b.type == "redacted_thinking":
                    blocks[ev.index] = {"type": "redacted_thinking", "data": b.data}
            elif ev.type == "content_block_delta" and ev.index in blocks:
                d, blk = ev.delta, blocks[ev.index]
                if d.type == "input_json_delta":
                    raw[ev.index].append(d.partial_json)
                elif d.type == "text_delta":
                    blk["text"] += d.text
                elif d.type == "thinking_delta":
                    blk["thinking"] += d.thinking
                elif d.type == "signature_delta":
                    blk["signature"] += d.signature
            elif ev.type == "content_block_stop" and ev.index in raw:
                blk = blocks[ev.index]
                try:
                    blk["input"] = json.loads("".join(raw[ev.index]) or "{}")
                    bad = False
                except ValueError:
                    bad = True
                call = on_tool("stop", blk, bad)
                calls.append(call)
                if call["ok"] and abort_on:
                    aborted = True
                    break
            elif ev.type == "message_delta":
                stop = getattr(ev.delta, "stop_reason", None)
                out = getattr(getattr(ev, "usage", None), "output_tokens", None)
                if out is not None:
                    usage["output_tokens"] = out
    except Exception as e:  # recorded, never retried
        error, status = f"{type(e).__name__}: {e}", getattr(e, "status_code", None)
    finally:
        if stream is not None:
            stream.close()
    return [blocks[i] for i in sorted(blocks)], calls, usage, stop, aborted, error, status


def run_episode(client, model, effort, case, tools, system, max_turns=5, abort=True, max_tokens=16000, timeout=120,
                clock=time.monotonic, gate=None, canonical=None, mock=None):
    """One task, as many requests as it takes. gate() is called before each request; its wait is not timed.
    A variant connector passes canonical(name, input) -> (logical tool, canonical input) so the unchanged answer keys
    still apply, and mock(name, input, logical, canonical input) -> result text."""
    ep = {"model": model, "effort": effort, "case": case["id"], "tier": case["tier"], "outcome": None, "error": None,
          "turns": [], "calls": [], "t_first_call": None, "t_correct": None, "calls_before_correct": None,
          "turns_to_correct": None, "wrong_writes": 0, "invalid": None, "error_status": None, "cost_est": 0.0, "request_ids": []}
    messages, skipped, t0 = runner.build_messages(case), 0.0, None
    now = lambda: clock() - t0 - skipped

    for turn in range(1, max_turns + 1):
        if gate:
            g = clock()
            gate()
            if t0 is not None:
                skipped += clock() - g
        if t0 is None:
            t0 = clock()
        t_send, turn_calls, started = now(), [], {}

        def on_tool(kind, blk, bad=False):
            if kind == "start":
                started[blk["id"]] = now()
                return None
            logical, cinput = canonical(blk["name"], blk["input"]) if canonical else (connectors.logical(blk["name"]), blk["input"])
            call = {"turn": turn, "tool": blk["name"], "input": blk["input"], "bad_json": bad, "t_tool_start": started[blk["id"]],
                    "t_tool_complete": now(), "read_only": is_read_only(logical), "canonical": [logical, cinput]}
            call["score"] = None if bad else scoring.score(case, logical, cinput)
            call["ok"] = bool(call["score"] and call["score"]["pass"])
            return call

        content, calls, usage, stop, aborted, error, status = _turn(client, model, effort, messages, tools, system, clock, on_tool,
                                                            abort, max_tokens, timeout)
        ep["calls"] += calls
        in_tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        out_tokens = usage.get("output_tokens")
        if out_tokens is None:  # aborted before message_delta
            out_tokens = sum(len(json.dumps(b)) for b in content) / runner.CHARS_PER_TOKEN
        ep["turns"].append({"turn": turn, "t_send": t_send, "usage": usage, "stop_reason": stop, "aborted": aborted})
        ep["cost_est"] += runner.cost(model, in_tokens, out_tokens) if model in runner.PRICES else 0.0
        if usage.get("cache_creation_input_tokens") or usage.get("cache_read_input_tokens"):
            ep["invalid"] = "cache_used"
        if calls and ep["t_first_call"] is None:
            ep["t_first_call"] = calls[0]["t_tool_complete"]
        green = next((c for c in ep["calls"] if c["ok"]), None)
        if green:
            ep["outcome"], ep["t_correct"], ep["turns_to_correct"] = "solved", green["t_tool_complete"], green["turn"]
            ep["calls_before_correct"] = ep["calls"].index(green)
            break
        if error:
            ep["outcome"], ep["error"], ep["error_status"] = "error", error, status
            break
        tool_uses = [b for b in content if b["type"] == "tool_use"]
        if stop == "max_tokens":
            ep["outcome"] = "max_tokens"
            break
        if not tool_uses:
            text = " ".join(b["text"] for b in content if b["type"] == "text").strip()
            ep["outcome"] = "clarification" if text.endswith("?") else "gave_up"
            break
        results = []
        for blk, call in zip(tool_uses, calls):
            m = clock()
            body = "Invalid JSON arguments." if call["bad_json"] else (mock or (lambda n, i, l, c: mock_result(l, c)))(
                blk["name"], blk["input"], *call["canonical"])
            skipped += clock() - m
            results.append({"type": "tool_result", "tool_use_id": blk["id"], "content": body,
                            "is_error": bool(call["bad_json"]) or body.startswith('{"error"')})
        messages = messages + [{"role": "assistant", "content": [b for b in content if b["type"] != "text" or b["text"]]},
                               {"role": "user", "content": results}]
    else:
        ep["outcome"] = "max_turns"
    ep["wrong_writes"] = sum(1 for c in ep["calls"] if not c["ok"] and not c["read_only"])
    return ep


def run_all_episodes(client, models, efforts, cases, reps, seed, out_path, budget_usd, tools, system, max_turns=5,
                     abort=True, log=print, tpm=None, resume_rows=None):
    budget = runner.Budget(Path(out_path).with_suffix(".spend.json"), budget_usd)
    trials = runner.plan_trials(models, efforts, cases, reps, seed)
    if resume_rows:  # every finished episode counts as done; only errors are repeated
        trials = runner.remaining_trials(trials, [{**r, "outcome": "tool_call"} for r in resume_rows if r["outcome"] != "error"])
        log(f"resume: {len(trials)} episodes still to run")
    recent, est = deque(), 50000
    with open(out_path, "a") as f:
        for i, (model, effort, case) in enumerate(trials):
            if budget.spent >= budget.limit:
                log(f"budget guard: ${budget.spent:.2f} >= ${budget.limit:.2f}, stopping after {i} of {len(trials)} episodes")
                break
            gate = (lambda: (runner.pace(recent, tpm, est), recent.append((time.monotonic(), est)))) if tpm else None
            ep = run_episode(client, model, effort, case, tools, system, max_turns=max_turns, abort=abort, gate=gate)
            est = (ep["turns"][0]["usage"].get("input_tokens") if ep["turns"] else None) or est
            ep.update(trial=i, seed=seed)
            budget.add(ep["cost_est"])
            f.write(json.dumps(ep) + "\n")
            f.flush()
            log(report.format_episode_terminal(ep, budget.spent, i + 1, len(trials)))
