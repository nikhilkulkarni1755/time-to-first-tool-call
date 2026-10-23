"""Time a model to its first tool call (any connector), abort the request, score the call."""
import json
import random
import time
from collections import Counter, deque
from pathlib import Path

from . import connectors, report, scoring

# (input, output) USD per million tokens. Mythos price was not checked: assumed equal to Fable.
PRICES = {
    "claude-fable-5-1": (10, 50),
    "claude-mythos-5-1": (10, 50),
    "claude-opus-5-5": (4, 20),
    "claude-sonnet-5-5": (2, 10),
    "claude-haiku-5-5": (0.10, 0.50),
}
CHARS_PER_TOKEN = 3.5  # conservative: the early abort loses the exact output token count


def cost(model, input_tokens, output_tokens):
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1e6


def build_messages(case):
    notes = "\n".join(f"- {n}" for n in case["notes"])
    return [{"role": "user", "content": f"Notes:\n{notes}\n\nTask: {case['task']}"}]


def run_trial(client, model, effort, case, tools, system, abort=True, max_tokens=16000, timeout=120):
    """One request. All times are seconds after t_send on the local monotonic clock."""
    rec = {"model": model, "effort": effort, "case": case["id"], "tier": case["tier"], "outcome": None,
           "error": None, "aborted": False, "t_first_event": None, "t_first_content": None,
           "t_thinking_start": None, "t_thinking_end": None, "t_tool_start": None, "t_tool_complete": None,
           "tool": None, "input": None, "score": None, "stop_reason": None, "request_id": None,
           "usage": {}, "output_chars": 0, "output_tokens": None, "thinking_tokens": None, "invalid": None}
    text, parts, tool_index, thinking_index = [], [], None, None
    stream = None
    t_send = time.monotonic()
    try:
        stream = client.messages.create(
            model=model, max_tokens=max_tokens, system=system, tools=tools, messages=build_messages(case),
            stream=True, timeout=timeout, output_config={"effort": effort})
        rec["request_id"] = getattr(getattr(stream, "response", None), "headers", {}).get("request-id")
        for ev in stream:
            now = time.monotonic() - t_send
            if ev.type == "message_start":
                rec["t_first_event"] = now
                usage = ev.message.usage
                rec["usage"] = {k: getattr(usage, k, None) for k in
                                ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
            elif ev.type == "content_block_start":
                rec["t_first_content"] = rec["t_first_content"] or now
                block = ev.content_block
                if block.type == "thinking" and rec["t_thinking_start"] is None:
                    rec["t_thinking_start"], thinking_index = now, ev.index
                elif block.type == "tool_use" and tool_index is None:
                    rec["t_tool_start"], tool_index, rec["tool"] = now, ev.index, block.name
            elif ev.type == "content_block_delta":
                d = ev.delta
                if d.type == "input_json_delta" and ev.index == tool_index:
                    parts.append(d.partial_json)
                rec["output_chars"] += len(getattr(d, "partial_json", None) or getattr(d, "thinking", None)
                                           or getattr(d, "text", None) or "")
                if d.type == "text_delta":
                    text.append(d.text)
            elif ev.type == "content_block_stop":
                if ev.index == thinking_index:
                    rec["t_thinking_end"] = now
                if ev.index == tool_index:
                    rec["t_tool_complete"] = now
                    if abort:
                        rec["aborted"] = True
                        break
            elif ev.type == "message_delta":
                rec["stop_reason"] = getattr(ev.delta, "stop_reason", None)
                usage = getattr(ev, "usage", None)
                rec["output_tokens"] = getattr(usage, "output_tokens", None)
                rec["thinking_tokens"] = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None)
    except Exception as e:  # recorded, never retried
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        if stream is not None:
            stream.close()
    _classify(rec, case, "".join(text).strip(), "".join(parts))
    return rec


def _classify(rec, case, text, raw_input):
    usage = rec["usage"]
    if usage.get("cache_creation_input_tokens") or usage.get("cache_read_input_tokens"):
        rec["invalid"] = "cache_used"
    if rec["t_tool_complete"] is not None:
        try:
            rec["input"] = json.loads(raw_input or "{}")
        except ValueError:
            rec["outcome"] = "bad_json"
            return
        rec["outcome"] = "tool_call"
        rec["score"] = scoring.score(case, connectors.logical(rec["tool"]), rec["input"])
    elif rec["error"]:
        rec["outcome"] = "error"
    elif rec["stop_reason"] == "max_tokens" or rec["tool"]:
        rec["outcome"] = "max_tokens"
    elif text:
        rec["outcome"] = "clarification" if text.endswith("?") else "other_text"
    else:
        rec["outcome"] = "no_output"
    in_tokens = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    out_tokens = rec["output_tokens"] if rec["output_tokens"] is not None else rec["output_chars"] / CHARS_PER_TOKEN
    rec["cost_est"] = cost(rec["model"], in_tokens, out_tokens) if rec["model"] in PRICES else 0.0


class Budget:
    """Running spend persisted to disk; the run stops when it reaches the limit."""

    def __init__(self, path, limit):
        self.path, self.limit = Path(path), limit
        self.spent = json.loads(self.path.read_text())["spent"] if self.path.exists() else 0.0

    def add(self, amount):
        self.spent += amount
        self.path.write_text(json.dumps({"spent": self.spent, "limit": self.limit}))


def plan_trials(models, efforts, cases, reps, seed):
    trials = [(m, e, c) for m in models for e in efforts for c in cases for _ in range(reps)]
    random.Random(seed).shuffle(trials)
    return trials


def remaining_trials(trials, resume_rows):
    """Drop trials already completed as tool calls in an earlier run (matched on model, effort, case; counted per rep)."""
    done = Counter((r["model"], r["effort"], r["case"]) for r in resume_rows if r["outcome"] == "tool_call")
    left = []
    for model, effort, case in trials:
        key = (model, effort, case["id"])
        if done[key]:
            done[key] -= 1
        else:
            left.append((model, effort, case))
    return left


def pace(recent, tpm, est_tokens, sleep=time.sleep, clock=time.monotonic):
    """Wait, outside the timed window, until another request fits under 90% of the input-tokens-per-minute limit."""
    while True:
        now = clock()
        while recent and now - recent[0][0] >= 60:
            recent.popleft()
        if sum(t for _, t in recent) + est_tokens <= tpm * 0.9:
            return
        sleep(0.25)


def run_all(client, models, efforts, cases, reps, seed, out_path, budget_usd, tools, system, abort=True, log=print,
            tpm=None, resume_rows=None):
    budget = Budget(Path(out_path).with_suffix(".spend.json"), budget_usd)
    trials = plan_trials(models, efforts, cases, reps, seed)
    if resume_rows:
        trials = remaining_trials(trials, resume_rows)
        log(f"resume: {len(trials)} trials still to run")
    recent, est = deque(), 50000
    with open(out_path, "a") as f:
        for i, (model, effort, case) in enumerate(trials):
            if budget.spent >= budget.limit:
                log(f"budget guard: ${budget.spent:.2f} >= ${budget.limit:.2f}, stopping after {i} of {len(trials)} trials")
                break
            if tpm:
                pace(recent, tpm, est)
            started = time.monotonic()
            rec = run_trial(client, model, effort, case, tools, system, abort=abort)
            est = rec["usage"].get("input_tokens") or est
            recent.append((started, est))
            rec.update(trial=i, seed=seed)
            budget.add(rec.get("cost_est", 0.0))
            f.write(json.dumps(rec) + "\n")
            f.flush()
            log(report.format_terminal(rec, budget.spent, i + 1, len(trials)))
