"""Connector variants (dials), arms, loud halts and the scorecard. No network, no spend."""
import json
from types import SimpleNamespace as NS

import pytest
from jsonschema import Draft202012Validator

from tests.test_episode import GOOD, SLACK, Script, turn
from tests.test_ttftc import CASES, DATA, FakeStream
from ttftc import connectors, dials, episode, leverage, report, runner, scoring

KEPT, DROPPED = leverage.distinct_arms(DATA["system"])
REGEX_SAMPLES = {"v2\\.14.*deploy.*finished": "v2.14 deploy finished", "fixed in #517": "Fixed in #517", "reviewed.*looks good": "Reviewed, looks good",
                 "deploy|shipped|released|went out": "deploy went out", "on-?call|rotation": "on-call", "invoice review": "invoice review",
                 "log-?(in|ged|out)": "login bug", "blocked.*api keys": "blocked on API keys", "migrat": "migrate docs site",
                 "checkout.*fixed": "checkout bug fixed", "fixed": "fixed", "^done$": "Done"}


def sample(matcher):
    if isinstance(matcher, dict):
        if "re" in matcher:
            return REGEX_SAMPLES[matcher["re"]]
        if "uuid" in matcher:
            return matcher["uuid"]
        if "any" in matcher:
            return sample(matcher["any"][0])
    return matcher


def canonical_calls():
    """(case, branch, a canonical input that the key accepts)."""
    for case in CASES.values():
        for branch in case["accept"]:
            inp = {}
            for key, matcher in branch["params"].items():
                if isinstance(matcher, dict) and "absent" in matcher:
                    continue
                cur = inp
                parts = key.split("|")[0].split(".")
                for p in parts[:-1]:
                    cur = cur.setdefault(p, {})
                cur[parts[-1]] = sample(matcher)
            yield case, branch, inp


def test_baseline_arm_is_exactly_the_shipped_connectors():
    base = KEPT["baseline"]
    assert base.tools == connectors.load_tools() and base.system == DATA["system"]


def test_arm_inventory_and_duplicates():
    assert len(KEPT) == 39 and set(DROPPED) == {"bad-D5", "bad-D7"}
    assert len({v.hash for v in KEPT.values()}) == len(KEPT)
    assert KEPT["baseline"].hash == dials.build({d: 1 for d in dials.DIALS}, DATA["system"]).hash  # deterministic


@pytest.mark.parametrize("arm", sorted(KEPT))
def test_every_arm_is_valid_and_every_answer_key_is_solvable_in_it(arm):
    v = KEPT[arm]
    names = [t["name"] for t in v.tools]
    assert len(names) == len(set(names)) and all(len(n) <= 64 for n in names)
    schemas = {t["name"]: t["input_schema"] for t in v.tools}
    for s in schemas.values():
        Draft202012Validator.check_schema(s)
    for case, branch, cinput in canonical_calls():
        name, vinput = v.express(branch["tool"], cinput)
        assert name in schemas, (arm, case["id"], branch["tool"], name)
        Draft202012Validator(schemas[name]).validate(vinput)
        logical, back = v.canonical(name, vinput)
        assert (logical, back) == (branch["tool"], cinput), (arm, case["id"], name, vinput, back)
        assert scoring.score(case, logical, back)["pass"], (arm, case["id"])


def test_dials_do_what_they_say():
    t = lambda arm: {x["name"]: x for x in KEPT[arm].tools}
    base, bogey, best = t("baseline"), t("bogeyman"), t("best")
    assert sum(n.startswith("mcp__github__") for n in base) == 20 and sum(n.startswith("mcp__github__") for n in bogey) == 96
    assert sum(n.startswith("mcp__github__") for n in best) == 7  # 5 core tools, issue_write split into create / update / close
    assert base["mcp__github__issue_read"]["description"] == "Get issue details"
    lead = bogey["mcp__github__item_read"]["description"]
    assert lead.startswith("Get issue details.") and len(lead) > 1000 and "mcp__github__issue_read" not in bogey
    assert bogey["mcp__linear__get_item"]["description"].startswith("Get issue details.") if "mcp__linear__get_item" in bogey else True
    assert "mcp__github__github_close_issue" in best and "mcp__github__github_comment_on_issue" in best
    assert "Do not call it before" in best["mcp__github__github_read_issue"]["description"]
    assert set(best["mcp__github__github_close_issue"]["input_schema"]["properties"]) == {"repo", "issue_number", "state_reason"}
    assert set(base["mcp__github__issue_write"]["input_schema"]["properties"]) >= {"owner", "repo", "method"}
    wide = t("bad-D6")["mcp__github__issue_write"]["input_schema"]["properties"]
    assert "dry_run" in wide and len(wide) > len(base["mcp__github__issue_write"]["input_schema"]["properties"])
    assert "account" in t("bad-D3")["mcp__github__issue_write"]["input_schema"]["properties"]
    assert KEPT["loo-D7"].system == DATA["system"] and "Do not look up an item" in KEPT["up-D7"].system
    scoped = t("up-D9")["mcp__linear__list_issues"]["description"]
    assert scoped.startswith("Linear (planned team work") and "GitHub instead" in scoped
    same = t("bad-D9")
    assert same["mcp__github__list_issues"]["description"] == same["mcp__linear__list_issues"]["description"] == "List issues."


def test_surface_size_ordering_matches_the_design():
    size = lambda arm: sum(len(json.dumps(x)) for x in KEPT[arm].tools)
    assert size("bogeyman") > size("bad-D1") > size("baseline") > size("up-D1") > size("minimal") * 0.9
    assert size("bad-D2") > size("baseline") > size("up-D6")


def test_d8_results_and_errors_per_level():
    raw = {"method": "get", "owner": "acme-labs", "repo": "billing-api", "issue_number": 391}
    out = {}
    for lvl in (0, 1, 2, 3):
        v = dials.build({"D8": lvl}, "s")
        out[lvl] = v.mock("mcp__github__issue_read", raw, "github.issue_read", raw)
    assert len(out[0]) > 5000 and out[1] == episode.mock_result("github.issue_read", raw)
    assert json.loads(out[2])["state"] == "open" and "next_step" in json.loads(out[3]) and "next_step" not in json.loads(out[2])
    bad = {"method": "get"}
    errs = [dials.build({"D8": lvl}, "s").mock("mcp__github__issue_read", bad, "github.issue_read", bad) for lvl in (0, 1, 2, 3)]
    assert json.loads(errs[0]) == {"error": "E_VALIDATION"} and "issue_number" in errs[2] and "Missing required" in errs[3] and len(errs[3]) > len(errs[2])
    assert not errs[1].startswith('{"error"')  # shipped behaviour: no validation


def run_arm(arm, client, case="V2", **kw):
    v = KEPT[arm]
    return episode.run_episode(client, "claude-haiku-5-5", "medium", CASES[case], v.tools, v.system, canonical=v.canonical, mock=v.mock, **kw)


def test_episode_in_a_variant_scores_through_the_canonical_key():
    close = ("mcp__github__github_close_issue", {"repo": "acme-labs/billing-api", "issue_number": "#391"}, "t1")
    ep = run_arm("best", Script(turn(close)))
    assert ep["outcome"] == "solved" and ep["calls"][0]["canonical"][0] == "github.issue_write"
    assert ep["calls"][0]["canonical"][1] == {"repo": "billing-api", "owner": "acme-labs", "issue_number": 391, "method": "update", "state": "closed"}
    wrong_service = ("mcp__linear__linear_get_issue", {"id": "PLT-1"}, "t1")
    c = Script(turn(wrong_service), turn(close))
    ep = run_arm("best", c)
    assert ep["calls_before_correct"] == 1 and "next_step" in c.sent[1]["messages"][2]["content"][0]["content"]


def test_validation_error_is_returned_as_an_error_result_and_recovered_from():
    partial = ("mcp__github__github_close_issue", {"issue_number": 391}, "t1")
    good = ("mcp__github__github_close_issue", {"repo": "acme-labs/billing-api", "issue_number": 391}, "t2")
    c = Script(turn(partial), turn(good))
    ep = run_arm("best", c)
    res = c.sent[1]["messages"][2]["content"][0]
    assert res["is_error"] is True and "repo" in res["content"] and ep["outcome"] == "solved" and ep["calls_before_correct"] == 1


# ---------- loud halts ----------

class FakeAPIError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


class Failing:
    """Succeeds `ok` times (a correct O1 call), then raises."""
    def __init__(self, ok, err):
        self.ok, self.err, self.requests = ok, err, 0
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.requests += 1
        if self.requests > self.ok:
            raise self.err
        return FakeStream(turn((SLACK, GOOD, "t")))


def run_arms(client, tmp_path, argv=("leverage", "run", "--models", "haiku"), limit=130.0, resume=None, reps=4, **kw):
    logs = []
    leverage.run_all_arms(client, KEPT, ["baseline"], ["claude-haiku-5-5"], ["low"], [CASES["O1"]], reps, 0, tmp_path / "r.jsonl", limit,
                          ledger_path=tmp_path / "ledger.json", log=logs.append, argv=list(argv), resume_rows=resume, **kw)
    return logs


def test_billing_error_halts_loudly_and_the_printed_command_resumes(tmp_path):
    c = Failing(2, FakeAPIError(400, "Your credit balance is too low to access the Anthropic API."))
    logs = []
    with pytest.raises(SystemExit) as e:
        logs = None
        run_arms(c, tmp_path)
    assert e.value.code == 3 and c.requests == 3  # the third request failed; the fourth episode was never attempted
    marker = json.loads((tmp_path / "r.jsonl.STOPPED.json").read_text())
    assert "credit balance" in marker["detail"] and marker["episodes_saved"] == 2
    assert marker["resume"].endswith(f"--resume {tmp_path / 'r.jsonl'} --out {tmp_path / 'r.jsonl'}")
    rows = report.load(tmp_path / "r.jsonl")
    assert [r["outcome"] for r in rows] == ["solved", "solved", "error"]
    # money added: resume finishes exactly the two missing episodes and appends to the same file
    ok = Failing(99, None)
    logs = run_arms(ok, tmp_path, resume=rows)
    assert ok.requests == 2 and len(report.load(tmp_path / "r.jsonl")) == 5
    assert any("resume: 2 episodes" in l for l in logs)


@pytest.mark.parametrize("status,msg", [(401, "invalid x-api-key"), (403, "forbidden"), (404, "model: claude-x"), (400, "Your credit balance is too low"),
                                        (429, "You have reached your specified API usage limits")])
def test_fatal_classification(status, msg):
    assert leverage.is_fatal(status, msg)
    c = Failing(0, FakeAPIError(status, msg))
    with pytest.raises(SystemExit) as e:
        run_arms(c, tmp_path_factory_dir(), reps=3)
    assert e.value.code == 3 and c.requests == 1


def tmp_path_factory_dir():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp())


def test_ordinary_errors_do_not_halt_until_five_in_a_row(tmp_path):
    assert not leverage.is_fatal(529, "overloaded") and not leverage.is_fatal(500, "internal error")
    c = Failing(0, FakeAPIError(529, "overloaded"))
    with pytest.raises(SystemExit) as e:
        run_arms(c, tmp_path, reps=10)
    assert e.value.code == 3 and c.requests == 5


def test_ledger_limit_halts_with_code_4(tmp_path):
    with pytest.raises(SystemExit) as e:
        run_arms(Failing(99, None), tmp_path, limit=0.0001, reps=5)
    assert e.value.code == 4 and json.loads((tmp_path / "r.jsonl.STOPPED.json").read_text())["reason"] == "LEDGER SPENT"
    assert len(report.load(tmp_path / "r.jsonl")) == 1
    assert json.loads((tmp_path / "ledger.json").read_text())["spent"] > 0.0001  # persisted: the next run starts from it


def test_resume_command_adds_resume_and_out_once():
    cmd = leverage.resume_command(["leverage", "run", "--models", "haiku", "--arms", "anchors"], "results/x.jsonl")
    assert cmd.count("--resume") == 1 and cmd.count("--out") == 1 and cmd.startswith(".venv/bin/python -m ttftc leverage run")
    again = leverage.resume_command(["leverage", "run", "--resume", "a.jsonl", "--out", "a.jsonl"], "a.jsonl")
    assert again.count("--resume") == 1 and again.count("--out") == 1


# ---------- scorecard ----------

def fake_row(arm, model, case, tier, t_correct, solved=True, before=0, lookup=False):
    call = {"ok": solved and before == 0, "read_only": lookup, "canonical": ["github.issue_write", {}], "turn": 1, "tool": "x", "t_tool_complete": t_correct, "score": None}
    return {"arm": arm, "model": model, "effort": "low", "case": case, "tier": tier, "outcome": "solved" if solved else "gave_up", "invalid": None,
            "t_correct": t_correct if solved else None, "t_first_call": 1.0, "calls_before_correct": before if solved else None, "wrong_writes": 0,
            "calls": [call], "turns": [{"usage": {"input_tokens": 1000}}], "cost_est": 0.01, "turns_to_correct": 1, "error": None}


def test_scorecard_and_dial_effects(tmp_path):
    rows = [fake_row("baseline", "m", "V2", "vague", t) for t in (2.0, 2.2, 2.4)] + [fake_row("bogeyman", "m", "V2", "vague", t) for t in (4.0, 5.0, 6.0)]
    rows += [fake_row("fix-D2", "m", "V2", "vague", t) for t in (3.0, 3.2, 3.4)] + [fake_row("fix-D2", "m", "V2", "vague", 0, solved=False)]
    rows += [fake_row("baseline", "m", "O1", "obvious", 1.0)]
    card = leverage.scorecard(rows, DATA["cases"])
    assert card[("baseline", "m")]["ttfctc_hard"] == 2.2 and card[("bogeyman", "m")]["ttfctc_hard"] == 5.0
    assert card[("fix-D2", "m")]["solved"] == 0.75 and card[("baseline", "m")]["ttfctc_obvious"] == 1.0
    eff = {e["arm"]: e for e in leverage.dial_effects(card)}
    assert eff["fix-D2"]["vs"] == "bogeyman" and eff["fix-D2"]["delta_s"] == pytest.approx(3.2 - 5.0) and eff["fix-D2"]["solved_pts"] == -25
    text = leverage.format_scorecard(card, list(eff.values()))
    assert "fix-D2" in text and "DIAL EFFECTS" in text
    leverage.render_html(card, list(eff.values()), tmp_path / "s.html")
    p = tmp_path / "rows.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    leverage.cmd_report(NS(jsonl=str(p)))
    assert (tmp_path / "rows.html").exists()
