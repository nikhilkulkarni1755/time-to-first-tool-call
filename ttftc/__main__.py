"""python -m ttftc run|report ..."""
import argparse
import json
import platform
import sys
import time
from pathlib import Path

from . import connectors, episode, leverage, report, runner

ROOT = Path(__file__).resolve().parent.parent
ALIASES = {"fable": "claude-fable-5-1", "mythos": "claude-mythos-5-1", "opus": "claude-opus-5-5",
           "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}


def dry_run(client, models, efforts, cases, reps, tools, system):
    """Project input cost per model from counted tokens (count_tokens is free); output is not included."""
    total = 0.0
    for model in models:
        try:
            tokens = client.messages.count_tokens(model=model, tools=tools, system=system,
                                                  messages=runner.build_messages(cases[0])).input_tokens
            how = "counted"
        except Exception as e:
            tokens, how = len(json.dumps(tools)) // 4, f"estimated chars/4; count_tokens failed: {str(e)[:70]}"
        n = len(efforts) * len(cases) * reps
        cost = runner.cost(model, tokens, 0) * n
        total += cost
        print(f"{model}: {tokens} input tokens ({how}), {n} trials, >= ${cost:.2f} input only")
    print(f"projected input-only total >= ${total:.2f}")


def cmd_run(args):
    import anthropic
    data = json.loads((ROOT / "cases.json").read_text())
    cases = [c for c in data["cases"] if not args.tiers or c["tier"] in args.tiers.split(",")]
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases.split(",")]
    models = [ALIASES.get(m, m) for m in args.models.split(",")]
    unknown = [m for m in models if m not in runner.PRICES]
    if unknown:
        sys.exit(f"unknown model(s) {unknown}; known: {sorted(runner.PRICES)}")
    efforts = args.efforts.split(",")
    tools = connectors.load_tools()
    client = anthropic.Anthropic(max_retries=0)
    if args.dry_run:
        return dry_run(client, models, efforts, cases, args.reps, tools, data["system"])
    out = Path(args.out or ROOT / "results" / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".meta.json").write_text(json.dumps({
        "started": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "host": platform.node(), "python": platform.python_version(),
        "anthropic_sdk": anthropic.__version__, "models": models, "efforts": efforts, "reps": args.reps, "seed": args.seed,
        "abort": not args.no_abort, "tools": len(tools), "cases": [c["id"] for c in cases], "system": data["system"]}, indent=2))
    client.messages.count_tokens(model=models[0], messages=[{"role": "user", "content": "hi"}])  # opens the connection
    resume = report.load(args.resume) if args.resume else None
    if args.episodes:
        episode.run_all_episodes(client, models, efforts, cases, args.reps, args.seed, out, args.budget, tools=tools,
                                 system=data["system"], max_turns=args.max_turns, abort=not args.no_abort, tpm=args.tpm,
                                 resume_rows=resume)
        report.render_episode_html(report.load(out), out.with_suffix(".html"))
    else:
        runner.run_all(client, models, efforts, cases, args.reps, args.seed, out, args.budget,
                       abort=not args.no_abort, tools=tools, system=data["system"], tpm=args.tpm, resume_rows=resume)
        report.render_html(report.load(out), out.with_suffix(".html"))
    print(f"results: {out}\nreport:  {out.with_suffix('.html')}")


def cmd_report(args):
    rows = report.load(args.jsonl)
    episodes = bool(rows) and "turns" in rows[0]
    (report.render_episode_html if episodes else report.render_html)(rows, Path(args.jsonl).with_suffix(".html"))
    for s in (report.summarize_episodes if episodes else report.summarize)(rows):
        print(s)


def main():
    p = argparse.ArgumentParser(prog="ttftc")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--models", required=True, help="comma list of: fable,mythos,opus,sonnet,haiku or model ids")
    r.add_argument("--efforts", default="medium")
    r.add_argument("--reps", type=int, default=1)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--tiers", help="obvious,vague,ambiguous")
    r.add_argument("--cases", help="comma list of case ids, e.g. O1,V2")
    r.add_argument("--budget", type=float, default=150.0, help="USD; the run stops when the estimated spend reaches it")
    r.add_argument("--out")
    r.add_argument("--tpm", type=int, help="input-tokens-per-minute limit of the model; requests are spaced to stay under 90%% of it")
    r.add_argument("--resume", help="earlier run JSONL: only run the trials it is missing (errors and shortfalls)")
    r.add_argument("--episodes", action="store_true", help="keep answering tool calls with mock results until one is fully green (TTFCTC)")
    r.add_argument("--max-turns", type=int, default=5, help="episode turn cap")
    r.add_argument("--no-abort", action="store_true", help="let the response finish (exact usage, same TTFTC)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_run)
    rp = sub.add_parser("report")
    rp.add_argument("jsonl")
    rp.set_defaults(fn=cmd_report)
    lv = sub.add_parser("leverage", help="connector-variant experiment (leverage.md)").add_subparsers(dest="lcmd", required=True)
    lv.add_parser("arms").set_defaults(fn=leverage.cmd_arms)
    lr = lv.add_parser("run")
    lr.add_argument("--arms", default="anchors", help="all | anchors | bad | fix | up | loo | comma list of arm ids")
    lr.add_argument("--models", required=True)
    lr.add_argument("--efforts", default="low,medium,high")
    lr.add_argument("--reps", type=int, default=1)
    lr.add_argument("--seed", type=int, default=0)
    lr.add_argument("--tiers")
    lr.add_argument("--cases")
    lr.add_argument("--budget", type=float, default=130.0, help="USD ledger limit shared by every leverage run")
    lr.add_argument("--ledger", default=str(leverage.LEDGER))
    lr.add_argument("--out")
    lr.add_argument("--tpm", type=int)
    lr.add_argument("--max-turns", type=int, default=5)
    lr.add_argument("--resume", help="earlier leverage JSONL (usually --out itself): only the missing episodes run")
    lr.add_argument("--dry-run", action="store_true")
    lr.set_defaults(fn=leverage.cmd_run)
    lrp = lv.add_parser("report")
    lrp.add_argument("jsonl")
    lrp.set_defaults(fn=leverage.cmd_report)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
