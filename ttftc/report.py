"""Terminal lines, summary stats and the HTML report (green / red per parameter)."""
import html
import json
import statistics
from collections import defaultdict

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _mark(ok):
    return f"{GREEN}ok {RESET}" if ok else f"{RED}BAD{RESET}"


def format_terminal(rec, spent, n, total):
    head = f"[{n}/{total}] {rec['model']} {rec['effort']} {rec['case']}({rec['tier']}) "
    if rec["outcome"] != "tool_call":
        return head + f"{RED}{rec['outcome']}{RESET} {rec['error'] or ''} spend=${spent:.2f}"
    s = rec["score"]
    lines = [head + f"first tool call {rec['t_tool_complete']:.2f}s (chosen at {rec['t_tool_start']:.2f}s) "
             f"{GREEN + 'PASS' if s['pass'] else RED + 'FAIL'}{RESET} spend=${spent:.2f}",
             f"    {_mark(s['tool']['ok'])} tool {s['tool']['actual']} (want {' | '.join(s['tool']['expected'])})"]
    for r in s["params"]:
        lines.append(f"    {_mark(r['ok'])} {r['path']} = {json.dumps(r['actual'])} {DIM}want {r['expected']}{RESET}")
    return "\n".join(lines)


def load(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def summarize(rows):
    """Stats per (model, effort, tier). 'any' = every trial that made a tool call; 'pass' = fully green only."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["effort"], r["tier"])].append(r)
    out = []
    for (model, effort, tier), rs in sorted(groups.items()):
        calls = [r for r in rs if r["outcome"] == "tool_call" and not r["invalid"]]
        passed = [r for r in calls if r["score"]["pass"]]
        params = [p["ok"] for r in calls for p in r["score"]["params"]]
        out.append({"model": model, "effort": effort, "tier": tier, "n": len(rs),
                    "tool_calls": len(calls), "clarifications": sum(r["outcome"] == "clarification" for r in rs),
                    "other": sum(r["outcome"] not in ("tool_call", "clarification") for r in rs),
                    "passed": len(passed), "param_green": (sum(params) / len(params)) if params else None,
                    "any": _lat([r["t_tool_complete"] for r in calls]),
                    "chosen": _lat([r["t_tool_start"] for r in calls]),
                    "pass_lat": _lat([r["t_tool_complete"] for r in passed])})
    return out


def _lat(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)} if values else None


def _fmt(lat):
    return f"{lat['median']:.2f} ({lat['min']:.2f}-{lat['max']:.2f})" if lat else "-"


CSS = """body{font:14px system-ui;margin:24px;color:#1b1b1b}table{border-collapse:collapse;margin:12px 0}
td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}.ok{background:#d7f5dd}.bad{background:#f8d2d2}
.trial{margin:16px 0;padding:8px;border:1px solid #ddd}.hd{font-weight:600}code{font-size:12px}"""


def render_html(rows, path):
    parts = [f"<!doctype html><meta charset=utf-8><title>TTFTC report</title><style>{CSS}</style><h1>Time to first tool call</h1>",
             "<p>Latency is seconds from request sent to the first tool call completing, in any connector. "
             "<b>any</b> = every trial that made a tool call; <b>pass</b> = only fully green trials; <b>chosen</b> = when the tool name was known. "
             "Median (min-max).</p><table><tr><th>model</th><th>effort</th><th>tier</th><th>n</th><th>tool calls</th>"
             "<th>clarif.</th><th>other</th><th>pass</th><th>param green</th><th>any</th><th>chosen</th><th>pass latency</th></tr>"]
    for s in summarize(rows):
        pg = f"{s['param_green']:.0%}" if s["param_green"] is not None else "-"
        parts.append(f"<tr><td>{s['model']}</td><td>{s['effort']}</td><td>{s['tier']}</td><td>{s['n']}</td><td>{s['tool_calls']}</td>"
                     f"<td>{s['clarifications']}</td><td>{s['other']}</td><td>{s['passed']}/{s['tool_calls']}</td><td>{pg}</td>"
                     f"<td>{_fmt(s['any'])}</td><td>{_fmt(s['chosen'])}</td><td>{_fmt(s['pass_lat'])}</td></tr>")
    parts.append("</table><h2>Trials</h2>")
    for r in rows:
        t = f"{r['t_tool_complete']:.2f}s" if r["t_tool_complete"] is not None else "-"
        parts.append(f"<div class=trial><div class=hd>{r['model']} · {r['effort']} · {r['case']} ({r['tier']}) · {r['outcome']} · {t}"
                     f"{' · INVALID: ' + r['invalid'] if r['invalid'] else ''}{' · ' + html.escape(r['error']) if r['error'] else ''}</div>")
        if r["score"]:
            s = r["score"]
            parts.append("<table><tr><th>parameter</th><th>actual</th><th>expected</th></tr>"
                         f"<tr class={'ok' if s['tool']['ok'] else 'bad'}><td>tool</td><td>{html.escape(s['tool']['actual'])}</td>"
                         f"<td>{html.escape(' | '.join(s['tool']['expected']))}</td></tr>")
            for p in s["params"]:
                parts.append(f"<tr class={'ok' if p['ok'] else 'bad'}><td>{html.escape(p['path'])}</td>"
                             f"<td><code>{html.escape(json.dumps(p['actual']))}</code></td><td>{html.escape(p['expected'])}"
                             f"{' (' + p['reason'] + ')' if p['reason'] else ''}</td></tr>")
            parts.append("</table>")
        parts.append("</div>")
    with open(path, "w") as f:
        f.write("".join(parts))


# ---------- episodes (time to first CORRECT tool call) ----------

def format_episode_terminal(ep, spent, n, total):
    head = f"[{n}/{total}] {ep['model']} {ep['effort']} {ep['case']}({ep['tier']}) "
    if ep["outcome"] == "solved":
        head += f"{GREEN}SOLVED{RESET} correct call at {ep['t_correct']:.2f}s after {ep['calls_before_correct']} wrong call(s)"
    else:
        head += f"{RED}{ep['outcome']}{RESET} {ep['error'] or ''}"
    lines = [head + f" turns={len(ep['turns'])} wrong_writes={ep['wrong_writes']} spend=${spent:.2f}"]
    for c in ep["calls"]:
        lines.append(f"    {_mark(c['ok'])} t={c['t_tool_complete']:.2f}s turn {c['turn']} {c['tool']}{' (read-only)' if c['read_only'] else ''}")
        for r in (c["score"] or {"params": []})["params"] if c["score"] and c["score"]["tool"]["ok"] else []:  # a wrong tool has no meaningful grid
            if not r["ok"]:
                lines.append(f"        {RED}BAD{RESET} {r['path']} = {json.dumps(r['actual'])} {DIM}want {r['expected']}{RESET}")
    return "\n".join(lines)


def summarize_episodes(rows):
    """Stats per (model, effort, tier). Times are over solved episodes only; unsolved ones are counted, never timed."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["effort"], r["tier"])].append(r)
    out = []
    for (model, effort, tier), rs in sorted(groups.items()):
        rs = [r for r in rs if not r["invalid"]]
        solved = [r for r in rs if r["outcome"] == "solved"]
        first = [r for r in rs if r["t_first_call"] is not None]
        out.append({"model": model, "effort": effort, "tier": tier, "n": len(rs), "solved": len(solved),
                    "first_try": sum(r["calls_before_correct"] == 0 for r in solved),
                    "unsolved": {o: sum(r["outcome"] == o for r in rs) for o in sorted({r["outcome"] for r in rs} - {"solved"})},
                    "wrong_writes": sum(r["wrong_writes"] for r in rs), "cost": sum(r["cost_est"] for r in rs),
                    "ttfctc": _lat([r["t_correct"] for r in solved]), "ttftc": _lat([r["t_first_call"] for r in first]),
                    "calls_before": _lat([r["calls_before_correct"] for r in solved]),
                    "turns": _lat([r["turns_to_correct"] for r in solved])})
    return out


def render_episode_html(rows, path):
    parts = [f"<!doctype html><meta charset=utf-8><title>TTFCTC report</title><style>{CSS}</style><h1>Time to first correct tool call</h1>",
             "<p>TTFCTC = seconds from the first request until a tool call completes with every check green (mock execution and pacing not timed). "
             "Times are over solved episodes, median (min-max); unsolved episodes are counted in the last column, not timed. "
             "<b>TTFTC</b> = the old metric (first call of any kind) on the same episodes.</p>"
             "<table><tr><th>model</th><th>effort</th><th>tier</th><th>n</th><th>solved</th><th>green on 1st call</th>"
             "<th>TTFCTC</th><th>TTFTC</th><th>wrong calls before correct</th><th>turns to correct</th><th>wrong writes</th><th>unsolved</th></tr>"]
    for s in summarize_episodes(rows):
        parts.append(f"<tr><td>{s['model']}</td><td>{s['effort']}</td><td>{s['tier']}</td><td>{s['n']}</td><td>{s['solved']}</td>"
                     f"<td>{s['first_try']}</td><td>{_fmt(s['ttfctc'])}</td><td>{_fmt(s['ttftc'])}</td>"
                     f"<td>{_fmt(s['calls_before']) if s['calls_before'] else '-'}</td><td>{_fmt(s['turns']) if s['turns'] else '-'}</td>"
                     f"<td>{s['wrong_writes']}</td><td>{html.escape(json.dumps(s['unsolved']))}</td></tr>")
    parts.append("</table><h2>Episodes</h2>")
    for r in rows:
        t = f"{r['t_correct']:.2f}s" if r["t_correct"] is not None else "-"
        parts.append(f"<div class=trial><div class=hd>{r['model']} · {r['effort']} · {r['case']} ({r['tier']}) · {r['outcome']} · {t} · "
                     f"{len(r['turns'])} turn(s){' · INVALID: ' + r['invalid'] if r['invalid'] else ''}"
                     f"{' · ' + html.escape(r['error']) if r['error'] else ''}</div>")
        for c in r["calls"]:
            parts.append(f"<table><tr class={'ok' if c['ok'] else 'bad'}><th>turn {c['turn']} · {c['t_tool_complete']:.2f}s</th>"
                         f"<th colspan=2>{html.escape(c['tool'])}{' (read-only)' if c['read_only'] else ''}</th></tr>")
            for p in (c["score"] or {"params": []})["params"] if c["score"] and c["score"]["tool"]["ok"] else []:
                parts.append(f"<tr class={'ok' if p['ok'] else 'bad'}><td>{html.escape(p['path'])}</td>"
                             f"<td><code>{html.escape(json.dumps(p['actual']))}</code></td><td>{html.escape(p['expected'])}"
                             f"{' (' + p['reason'] + ')' if p['reason'] else ''}</td></tr>")
            parts.append("</table>")
        parts.append("</div>")
    with open(path, "w") as f:
        f.write("".join(parts))
