"""Leverage experiment (leverage.md): connector variants (arms) x models x efforts x the 15 prompts, run as episodes.

Billing, credit, auth and access errors halt the run loudly (exit code 3) with a ready-to-paste resume command;
a spent ledger halts it too (exit code 4). Nothing keeps spending after either.
"""
import json
import random
import re
import shlex
import statistics
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from . import dials, episode, report, runner

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "results" / "leverage" / "spend.json"
FATAL_STATUS = {401, 402, 403, 404}
FATAL_TEXT = re.compile(r"credit balance|billing|insufficient (funds|credit)|spend limit|usage limit|payment|suspended|disabled|invalid x-api-key|authentication", re.I)
HARD = ("vague", "ambiguous")
MAX_CONSECUTIVE_ERRORS = 5
GREEN, RED, BOLD, RESET = "\033[32m", "\033[31m", "\033[1m", "\033[0m"


# ---------- arms ----------

def all_arms():
    """{arm id: dial levels}. Arms whose built tools are identical to an earlier arm are dropped by `distinct_arms`."""
    base = {d: 1 for d in dials.DIALS}
    bogey, best, nudge = ({d: v for d in dials.DIALS} for v in (0, 3, 2))
    arms = {"baseline": base, "bogeyman": bogey, "best": best, "nudge": nudge, "minimal": {**{d: 1 for d in dials.DIALS}, "D1": 3, "D6": 3, "minimal": True}}
    for d in dials.DIALS:
        arms[f"bad-{d}"] = {**base, d: 0}
        arms[f"up-{d}"] = {**base, d: 3}
        arms[f"fix-{d}"] = {**bogey, d: 3}
        arms[f"loo-{d}"] = {**best, d: 1}
    return arms


def distinct_arms(system):
    """(kept {arm id: Variant}, dropped {arm id: id of the identical earlier arm})."""
    kept, dropped, seen = {}, {}, {}
    for arm_id, levels in all_arms().items():
        v = dials.build(levels, system)
        if v.hash in seen:
            dropped[arm_id] = seen[v.hash]
        else:
            seen[v.hash], kept[arm_id] = arm_id, v
    return kept, dropped


def select(spec, kept):
    """'all', 'anchors', a family ('bad','fix','up','loo') or a comma list of arm ids."""
    groups = {"anchors": ["baseline", "bogeyman", "best", "nudge", "minimal"]}
    chosen = []
    for part in spec.split(","):
        names = groups.get(part) or ([a for a in kept if a.startswith(part + "-")] if part in ("bad", "fix", "up", "loo") else
                                    list(kept) if part == "all" else [part])
        chosen += [n for n in names if n not in chosen]
    unknown = [n for n in chosen if n not in kept]
    if unknown:
        sys.exit(f"unknown or duplicate arm(s) {unknown}; see `leverage arms`")
    return chosen


# ---------- fatal stops ----------

def is_fatal(status, message):
    return status in FATAL_STATUS or bool(FATAL_TEXT.search(message or ""))


def resume_command(argv, out):
    parts = [a for a in argv]
    if "--resume" not in parts:
        parts += ["--resume", str(out)]
    if "--out" not in parts:
        parts += ["--out", str(out)]
    return ".venv/bin/python -m ttftc " + shlex.join(parts)


def halt(reason, detail, code, out, spent, limit, done, total, argv, log):
    """Loud, final: banner, terminal bell, marker file, exit. Nothing further is sent."""
    cmd = resume_command(argv, out)
    banner = "\n".join(["", "!" * 78, f"{RED}{BOLD}  RUN STOPPED: {reason}{RESET}", "!" * 78, f"  {detail}",
                        f"  ledger: ${spent:.2f} of ${limit:.2f}   episodes saved in {out}: {done} / {total}",
                        "  The autonomous run has halted and will not send anything else.",
                        "  Fix it (credit / key / access / budget), then resume exactly where it stopped with:", f"    {cmd}", "!" * 78, "\a"])
    log(banner)
    Path(str(out) + ".STOPPED.json").write_text(json.dumps({"reason": reason, "detail": detail, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                            "ledger_spent": spent, "ledger_limit": limit, "episodes_saved": done, "resume": cmd}, indent=2))
    raise SystemExit(code)


# ---------- the run ----------

def run_all_arms(client, variants, arm_ids, models, efforts, cases, reps, seed, out_path, budget_usd, max_turns=5, tpm=None,
                 resume_rows=None, ledger_path=LEDGER, log=print, argv=()):
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    budget = runner.Budget(ledger_path, budget_usd)
    pairs = [(a, m) for a in arm_ids for m in models]
    trials = runner.plan_trials(pairs, efforts, cases, reps, seed)
    if resume_rows:  # every finished episode counts as done; errors are repeated
        trials = runner.remaining_trials(trials, [{**r, "model": (r["arm"], r["model"]), "outcome": "tool_call"} for r in resume_rows if r["outcome"] != "error"])
        log(f"resume: {len(trials)} episodes still to run")
    recent, est, errors, done = deque(), 50000, 0, len(resume_rows or [])
    total = len(trials) + done
    with open(out_path, "a") as f:
        for i, ((arm, model), effort, case) in enumerate(trials):
            if budget.spent >= budget.limit:
                halt("LEDGER SPENT", f"estimated spend ${budget.spent:.2f} reached the ${budget.limit:.2f} limit before episode {done + 1}", 4,
                     out_path, budget.spent, budget.limit, done, total, argv, log)
            v = variants[arm]
            gate = (lambda: (runner.pace(recent, tpm, est), recent.append((time.monotonic(), est)))) if tpm else None
            ep = episode.run_episode(client, model, effort, case, v.tools, v.system, max_turns=max_turns, gate=gate, canonical=v.canonical, mock=v.mock)
            est = (ep["turns"][0]["usage"].get("input_tokens") if ep["turns"] else None) or est
            ep.update(trial=i, seed=seed, arm=arm, arm_hash=v.hash, levels=v.levels)
            budget.add(ep["cost_est"])
            f.write(json.dumps(ep) + "\n")
            f.flush()
            log(f"{arm} " + report.format_episode_terminal(ep, budget.spent, done + 1, total))
            if ep["outcome"] == "error":
                errors += 1
                if is_fatal(ep["error_status"], ep["error"]):
                    halt("BILLING / AUTH / ACCESS ERROR", f"HTTP {ep['error_status']}: {ep['error'][:300]}", 3, out_path, budget.spent, budget.limit,
                         done, total, argv, log)
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    halt("TOO MANY CONSECUTIVE ERRORS", f"{errors} in a row; last: {ep['error'][:300]}", 3, out_path, budget.spent, budget.limit,
                         done, total, argv, log)
            else:
                errors, done = 0, done + 1


# ---------- scorecard ----------

def _p(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def _med(xs):
    return statistics.median(xs) if xs else None


def _boot(a, b, n=1000, seed=0):
    """95% interval of median(b) - median(a), unpaired bootstrap."""
    if not a or not b:
        return None
    rnd, diffs = random.Random(seed), []
    for _ in range(n):
        diffs.append(statistics.median(rnd.choices(b, k=len(b))) - statistics.median(rnd.choices(a, k=len(a))))
    diffs.sort()
    return diffs[int(0.025 * n)], diffs[int(0.975 * n)]


def _accepted_connectors(cases):
    return {c["id"]: {b["tool"].split(".")[0] for b in c["accept"]} for c in cases}


def scorecard(rows, cases):
    acc = _accepted_connectors(cases)
    groups = defaultdict(list)
    for r in rows:
        if not r["invalid"] and r["outcome"] != "error":
            groups[(r["arm"], r["model"])].append(r)
    out = {}
    for key, rs in groups.items():
        solved = [r for r in rs if r["outcome"] == "solved"]
        hard = [r["t_correct"] for r in solved if r["tier"] in HARD]
        first = [r["calls"][0] for r in rs if r["calls"]]
        out[key] = {"n": len(rs), "solved": len(solved) / len(rs), "first_try": sum(r["calls_before_correct"] == 0 for r in solved) / len(rs),
                    "ttfctc_hard": _med(hard), "ttfctc_hard_p90": _p(hard, 0.9), "ttfctc_hard_raw": hard,
                    "ttfctc_obvious": _med([r["t_correct"] for r in solved if r["tier"] == "obvious"]),
                    "ttftc": _med([r["t_first_call"] for r in rs if r["t_first_call"] is not None]),
                    "lookup_first": sum((not c["ok"]) and c["read_only"] for c in first) / max(1, len(first)),
                    "wrong_service": sum(c["canonical"][0].split(".")[0] not in acc[r["case"]] for r, c in ((r, r["calls"][0]) for r in rs if r["calls"])) / max(1, len(first)),
                    "calls_before": _med([r["calls_before_correct"] for r in solved]), "wrong_writes": sum(r["wrong_writes"] for r in rs),
                    "input_tokens": _med([r["turns"][0]["usage"].get("input_tokens") for r in rs if r["turns"]]),
                    "cost": sum(r["cost_est"] for r in rs) / len(rs)}
    return out


def dial_effects(card):
    """Per model and dial: how far each single-dial arm moves hard-tier TTFCTC from its reference arm."""
    refs = {"bad": "baseline", "up": "baseline", "fix": "bogeyman", "loo": "best"}
    rows = []
    for (arm, model), c in sorted(card.items()):
        fam, _, dial = arm.partition("-")
        ref = card.get((refs.get(fam), model))
        if not dial or not ref or c["ttfctc_hard"] is None or ref["ttfctc_hard"] is None:
            continue
        ci = _boot(ref["ttfctc_hard_raw"], c["ttfctc_hard_raw"])
        rows.append({"model": model, "arm": arm, "vs": refs[fam], "delta_s": c["ttfctc_hard"] - ref["ttfctc_hard"],
                     "delta_pct": (c["ttfctc_hard"] - ref["ttfctc_hard"]) / ref["ttfctc_hard"], "ci": ci,
                     "solved_pts": 100 * (c["solved"] - ref["solved"]), "lookup_pts": 100 * (c["lookup_first"] - ref["lookup_first"])})
    return rows


def format_scorecard(card, effects):
    f = lambda v, fmt="{:.2f}": "-" if v is None else fmt.format(v)
    lines = [f"{'arm':12}{'model':20}{'n':>4}{'solved':>8}{'1st-try':>8}{'TTFCTC v+a':>11}{'p90':>7}{'obv':>6}{'TTFTC':>7}{'lookup1':>8}{'wrongsvc':>9}{'tok':>8}{'$/ep':>8}"]
    for (arm, model), c in sorted(card.items()):
        lines.append(f"{arm:12}{model:20}{c['n']:>4}{c['solved']:>8.0%}{c['first_try']:>8.0%}{f(c['ttfctc_hard']):>11}{f(c['ttfctc_hard_p90']):>7}"
                     f"{f(c['ttfctc_obvious']):>6}{f(c['ttftc']):>7}{c['lookup_first']:>8.0%}{c['wrong_service']:>9.0%}{f(c['input_tokens'], '{:.0f}'):>8}{c['cost']:>8.3f}")
    lines += ["", "DIAL EFFECTS (hard-tier TTFCTC, seconds and %, vs the reference arm; ci = 95% bootstrap of the median change)"]
    for e in effects:
        ci = "n/a" if e["ci"] is None else f"[{e['ci'][0]:+.2f}, {e['ci'][1]:+.2f}]"
        lines.append(f"  {e['model']:20}{e['arm']:10} vs {e['vs']:9} {e['delta_s']:+7.2f}s {e['delta_pct']:+7.0%} ci {ci:18} solved {e['solved_pts']:+5.0f}pt lookup-first {e['lookup_pts']:+5.0f}pt")
    return "\n".join(lines)


def render_html(card, effects, path):
    th = "".join(f"<th>{h}</th>" for h in ("arm", "model", "n", "solved", "first try", "TTFCTC hard", "p90", "TTFCTC obvious", "TTFTC", "lookup-first", "wrong service", "input tokens", "$/episode"))
    f = lambda v, fmt="{:.2f}": "-" if v is None else fmt.format(v)
    body = "".join(f"<tr><td>{a}</td><td>{m}</td><td>{c['n']}</td><td>{c['solved']:.0%}</td><td>{c['first_try']:.0%}</td><td>{f(c['ttfctc_hard'])}</td>"
                   f"<td>{f(c['ttfctc_hard_p90'])}</td><td>{f(c['ttfctc_obvious'])}</td><td>{f(c['ttftc'])}</td><td>{c['lookup_first']:.0%}</td>"
                   f"<td>{c['wrong_service']:.0%}</td><td>{f(c['input_tokens'], '{:.0f}')}</td><td>{c['cost']:.3f}</td></tr>" for (a, m), c in sorted(card.items()))
    eff = "".join(f"<tr><td>{e['model']}</td><td>{e['arm']}</td><td>{e['vs']}</td><td>{e['delta_s']:+.2f}s</td><td>{e['delta_pct']:+.0%}</td>"
                  f"<td>{'n/a' if e['ci'] is None else '[%+.2f, %+.2f]' % e['ci']}</td><td>{e['solved_pts']:+.0f}</td><td>{e['lookup_pts']:+.0f}</td></tr>" for e in effects)
    Path(path).write_text(f"<!doctype html><meta charset=utf-8><title>Leverage scorecard</title><style>{report.CSS}</style><h1>Leverage scorecard</h1>"
                          "<p>TTFCTC = seconds to the first fully green tool call, median over solved vague+ambiguous episodes. Unsolved episodes are never timed; see the solved column.</p>"
                          f"<table><tr>{th}</tr>{body}</table><h2>Dial effects</h2><table><tr><th>model</th><th>arm</th><th>vs</th><th>change</th><th>%</th><th>95% interval</th>"
                          f"<th>solved pts</th><th>lookup-first pts</th></tr>{eff}</table>")


# ---------- CLI ----------

def _setup(args):
    data = json.loads((ROOT / "cases.json").read_text())
    kept, dropped = distinct_arms(data["system"])
    return data, kept, dropped


def cmd_arms(args):
    data, kept, dropped = _setup(args)
    print(f"{len(kept)} distinct arms ({len(dropped)} dropped as identical to an earlier arm: {dropped})")
    for arm, v in kept.items():
        n = len(v.tools)
        chars = sum(len(json.dumps(t)) for t in v.tools)
        print(f"  {arm:12} {v.hash} tools={n:3} ~{chars / 2.75 / 1000:5.1f}k tokens  levels={ {k: x for k, x in v.levels.items() if x != 1} }")


def cmd_run(args):
    import anthropic
    from .__main__ import ALIASES
    data, kept, dropped = _setup(args)
    cases = [c for c in data["cases"] if not args.tiers or c["tier"] in args.tiers.split(",")]
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases.split(",")]
    arm_ids = select(args.arms, kept)
    models = [ALIASES.get(m, m) for m in args.models.split(",")]
    efforts = args.efforts.split(",")
    client = anthropic.Anthropic(max_retries=0)
    if args.dry_run:
        total = 0.0
        for arm in arm_ids:
            v = kept[arm]
            for model in models:
                try:
                    tokens, how = client.messages.count_tokens(model=model, tools=v.tools, system=v.system, messages=runner.build_messages(cases[0])).input_tokens, "counted"
                except Exception as e:
                    tokens, how = int(sum(len(json.dumps(t)) for t in v.tools) / 2.75), f"estimated; count_tokens failed: {str(e)[:50]}"
                cost = runner.cost(model, tokens, 0) * 1.4 * len(efforts) * len(cases) * args.reps
                total += cost
                print(f"{arm:12} {model:20} {tokens:7} tokens ({how}) {len(efforts) * len(cases) * args.reps} episodes  ~${cost:.2f}")
        print(f"projected total ~${total:.2f} (input only x1.4 for extra turns); ledger limit ${args.budget:.2f}")
        return
    out = Path(args.out or ROOT / "results" / "leverage" / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    resume = report.load(args.resume) if args.resume else None
    client.messages.count_tokens(model=models[0], messages=[{"role": "user", "content": "hi"}])  # fails loudly now on a bad key / credit, before any episode
    run_all_arms(client, kept, arm_ids, models, efforts, cases, args.reps, args.seed, out, args.budget, max_turns=args.max_turns, tpm=args.tpm,
                 resume_rows=resume, ledger_path=args.ledger, argv=sys.argv[1:])
    cmd_report(argparse_ns(jsonl=str(out)))
    print(f"results: {out}")


def argparse_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


def cmd_report(args):
    data = json.loads((ROOT / "cases.json").read_text())
    rows = report.load(args.jsonl)
    card = scorecard(rows, data["cases"])
    effects = dial_effects(card)
    print(format_scorecard(card, effects))
    render_html(card, effects, Path(args.jsonl).with_suffix(".html"))
