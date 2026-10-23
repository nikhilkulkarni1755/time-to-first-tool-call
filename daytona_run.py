"""Run the benchmark inside a Daytona sandbox and pull the results back.

    DAYTONA_API_KEY=... ANTHROPIC_API_KEY=... python daytona_run.py --models haiku --efforts low,medium,high

Every argument after the ones below is passed to `python -m ttftc run`.
Harness and requests then run on the sandbox's network, so latencies are measured from there, not from this machine.
"""
import argparse
import os
import shlex
import sys
import time
from pathlib import Path

from daytona import CreateSandboxFromSnapshotParams, Daytona, FileUpload

ROOT = Path(__file__).resolve().parent
SOURCES = ["cases.json", *map(str, (p.relative_to(ROOT) for p in sorted((ROOT / "ttftc").glob("*.py")))),
           *map(str, (p.relative_to(ROOT) for p in sorted((ROOT / "connectors").glob("*.json"))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", default="daytona-medium", help="2 vCPU / 4 GiB container (default)")
    ap.add_argument("--keep", action="store_true", help="do not delete the sandbox afterwards")
    ap.add_argument("--poll", type=int, default=20, help="seconds between progress checks")
    ap.add_argument("--resume", help="earlier run JSONL to upload; the sandbox only runs the trials it is missing")
    args, run_args = ap.parse_known_args()
    for var in ("DAYTONA_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            sys.exit(f"{var} is not set")

    daytona = Daytona()
    sandbox = daytona.create(CreateSandboxFromSnapshotParams(snapshot=args.snapshot, auto_stop_interval=0))
    print(f"sandbox {sandbox.id} ({args.snapshot})")
    try:
        sandbox.fs.upload_files([FileUpload(source=(ROOT / p).read_bytes(), destination=p) for p in SOURCES])
        if args.resume:
            sandbox.fs.upload_files([FileUpload(source=Path(args.resume).read_bytes(), destination="resume.jsonl")])
            run_args += ["--resume", "resume.jsonl"]
        setup = sandbox.process.exec("python3 -m venv .venv && .venv/bin/pip install -q anthropic", timeout=300)
        if setup.exit_code:
            sys.exit(f"setup failed:\n{setup.result}")
        cmd = f"mkdir -p results && (.venv/bin/python -m ttftc run {shlex.join(run_args)} --out results/run.jsonl; echo $? > run.done) > run.log 2>&1"
        sandbox.process.exec(f"nohup sh -c {shlex.quote(cmd)} > /dev/null 2>&1 &",
                             env={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]})
        shown = 0
        while True:
            time.sleep(args.poll)
            log = sandbox.process.exec("cat run.log 2>/dev/null").result.splitlines()
            print("\n".join(log[shown:]))
            shown = len(log)
            done = sandbox.process.exec("cat run.done 2>/dev/null").result.strip()
            if done:
                print(f"run exited with code {done}")
                break
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = ROOT / "results" / f"daytona-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in ("run.jsonl", "run.html", "run.meta.json", "run.spend.json"):
            try:
                sandbox.fs.download_file(f"results/{name}", str(out_dir / name))
            except Exception as e:
                print(f"could not download {name}: {e}")
        (out_dir / "sandbox.txt").write_text(f"id={sandbox.id}\nsnapshot={args.snapshot}\ntarget={getattr(sandbox, 'target', '')}\n")
        print(f"results in {out_dir}")
    finally:
        if args.keep:
            print(f"sandbox {sandbox.id} kept")
        else:
            daytona.delete(sandbox)


if __name__ == "__main__":
    main()
