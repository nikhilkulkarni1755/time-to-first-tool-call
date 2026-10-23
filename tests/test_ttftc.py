import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ttftc import connectors, report, runner, scoring

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "cases.json").read_text())
CASES = {c["id"]: c for c in DATA["cases"]}
TOOLS = connectors.load_tools()


# ---------- scoring ----------

def rows(result):
    return {r["path"]: r["ok"] for r in result["params"]}


def test_all_green():
    r = scoring.score(CASES["O2"], "github.add_issue_comment",
                      {"owner": "acme-labs", "repo": "billing-api", "issue_number": 482, "body": "Fixed in #517"})
    assert r["pass"] and r["tool"]["ok"] and all(rows(r).values())


def test_each_wrong_param_is_red_and_others_stay_green():
    r = scoring.score(CASES["O2"], "github.add_issue_comment",
                      {"owner": "acme-labs", "repo": "billing-web", "issue_number": "482", "body": "Fixed in #517"})
    assert not r["pass"]
    assert rows(r) == {"owner": True, "repo": False, "issue_number": False, "body": True}  # string "482" is not the number 482


def test_missing_and_unexpected_parameters_are_red():
    r = scoring.score(CASES["O3"], "cloudflare.purge_cache", {"zone_id": "9f2c1d7e4b8a40c3a6e5d0f1b2c37a84", "tags": ["x"]})
    assert rows(r) == {"zone_id": True, "purge_everything": False, "tags.0": False}
    assert not r["pass"]


def test_wrong_tool_is_red_but_params_still_graded():
    r = scoring.score(CASES["O1"], "github.add_issue_comment", {"body": "v2.14 deploy finished"})
    assert not r["tool"]["ok"] and not r["pass"]


def test_uuid_ignores_dashes_and_alternatives():
    comment = CASES["O4"]
    a = scoring.score(comment, "notion.notion-create-comment",
                      {"page_id": "7c3e91a25b4d4f609a1e2d8b6c0f4e13", "markdown": "Reviewed, looks good"})
    assert a["pass"]
    b = scoring.score(comment, "notion.notion-create-comment",
                      {"page_id": "7c3e91a2-5b4d-4f60-9a1e-2d8b6c0f4e13",
                       "rich_text": [{"type": "text", "text": {"content": "Reviewed, looks good"}}]})
    assert b["pass"]
    c = scoring.score(comment, "notion.notion-create-comment",
                      {"page_id": "7c3e91a2-5b4d-4f60-9a1e-2d8b6c0f4e13", "markdown": "ok", "rich_text": [{"text": {"content": "x"}}]})
    assert not c["pass"]


def test_absent_matcher_flags_update_when_creating():
    ok = scoring.score(CASES["O5"], "linear.save_issue", {"title": "Fix login timeout", "team": "Platform"})
    assert ok["pass"]
    bad = scoring.score(CASES["O5"], "linear.save_issue", {"title": "Fix login timeout", "team": "PLT", "id": "PLT-77"})
    assert rows(bad)["id"] is False


def test_ambiguous_accepts_any_branch_with_its_own_key():
    gh = {"method": "create", "owner": "acme-labs", "repo": "auth-service", "title": "Login logs users out"}
    assert scoring.score(CASES["A1"], "github.issue_write", gh)["pass"]
    lin = {"team": "PLT", "title": "Login bug"}
    assert scoring.score(CASES["A1"], "linear.save_issue", lin)["pass"]
    wrong = scoring.score(CASES["A1"], "slack.slack_send_message", {"channel_id": "C03ENGQ77", "message": "login"})
    assert not wrong["tool"]["ok"]


def test_nested_property_and_allowed_optional():
    r = scoring.score(CASES["A5"], "notion.notion-update-page",
                      {"page_id": "e5b1c8d3-6a29-4f07-b3d4-8c0a2e9f7b16", "command": "update_properties", "properties": {"Status": "Done"}})
    assert r["pass"] and "properties.Status" in rows(r)
    v2 = scoring.score(CASES["V2"], "github.issue_write",
                       {"method": "update", "owner": "acme-labs", "repo": "billing-api", "issue_number": 391,
                        "state": "closed", "state_reason": "completed"})
    assert v2["pass"]
    v2bad = scoring.score(CASES["V2"], "github.issue_write",
                          {"method": "update", "owner": "acme-labs", "repo": "billing-api", "issue_number": 391,
                           "state": "closed", "state_reason": "not_planned"})
    assert not v2bad["pass"]


def test_every_key_tool_and_param_exists_in_the_connectors():
    schemas = {connectors.logical(t["name"]): t["input_schema"]["properties"] for t in TOOLS}
    for case in CASES.values():
        for branch in case["accept"]:
            assert branch["tool"] in schemas, (case["id"], branch["tool"])
            props = schemas[branch["tool"]]
            for key in list(branch["params"]) + list(branch.get("allowed_optional") or {}):
                for alt in key.split("|"):
                    assert alt.split(".")[0] in props, (case["id"], branch["tool"], alt)


def test_connector_inventory():
    names = [t["name"] for t in TOOLS]
    assert len(names) == len(set(names)) == 92
    assert all(len(n) <= 64 for n in names)


# ---------- runner with a scripted stream ----------

class FakeStream:
    def __init__(self, events):
        self.events, self.consumed, self.closed = events, 0, False
        self.response = NS(headers={"request-id": "req_fake"})

    def __iter__(self):
        for ev in self.events:
            self.consumed += 1
            yield ev

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, events):
        self.stream, self.kwargs = FakeStream(events), None
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.kwargs = kw
        return self.stream


def ev(type, **kw):
    return NS(type=type, **kw)


def tool_events(name, payload, thinking=True):
    s = json.dumps(payload)
    out = [ev("message_start", message=NS(usage=NS(input_tokens=30000, cache_creation_input_tokens=0, cache_read_input_tokens=0)))]
    if thinking:
        out += [ev("content_block_start", index=0, content_block=NS(type="thinking")),
                ev("content_block_delta", index=0, delta=NS(type="thinking_delta", thinking="hmm " * 10)),
                ev("content_block_stop", index=0)]
    out += [ev("content_block_start", index=1, content_block=NS(type="tool_use", name=name)),
            ev("content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json=s[:10])),
            ev("content_block_delta", index=1, delta=NS(type="input_json_delta", partial_json=s[10:])),
            ev("content_block_stop", index=1),
            ev("content_block_start", index=2, content_block=NS(type="text")),
            ev("content_block_delta", index=2, delta=NS(type="text_delta", text="done")),
            ev("content_block_stop", index=2),
            ev("message_delta", delta=NS(stop_reason="tool_use"))]
    return out


GOOD = {"channel_id": "C07RLS4K2", "message": "v2.14 deploy finished"}


def test_abort_stops_at_first_tool_call_and_scores_it():
    c = FakeClient(tool_events("mcp__slack__slack_send_message", GOOD))
    rec = runner.run_trial(c, "claude-haiku-5-5", "medium", CASES["O1"], TOOLS, DATA["system"])
    assert rec["outcome"] == "tool_call" and rec["aborted"] and c.stream.closed
    assert c.stream.consumed == 8  # stopped at the tool block's stop event, before the trailing text block
    assert rec["score"]["pass"]
    assert rec["t_thinking_start"] is not None and rec["t_tool_start"] <= rec["t_tool_complete"]
    assert c.kwargs["output_config"] == {"effort": "medium"} and c.kwargs["stream"] is True
    assert "cache_control" not in json.dumps(c.kwargs["tools"]) and "temperature" not in c.kwargs
    assert rec["request_id"] == "req_fake" and rec["invalid"] is None and rec["cost_est"] > 0


def test_no_abort_reads_to_the_end():
    c = FakeClient(tool_events("mcp__slack__slack_send_message", GOOD))
    rec = runner.run_trial(c, "claude-haiku-5-5", "low", CASES["O1"], TOOLS, DATA["system"], abort=False)
    assert not rec["aborted"] and c.stream.consumed == len(c.stream.events) and rec["stop_reason"] == "tool_use"


def test_any_connector_counts_even_when_wrong():
    c = FakeClient(tool_events("mcp__github__issue_write", {"method": "create", "owner": "x", "repo": "y", "title": "z"}, thinking=False))
    rec = runner.run_trial(c, "claude-haiku-5-5", "medium", CASES["O1"], TOOLS, DATA["system"])
    assert rec["outcome"] == "tool_call" and rec["t_tool_complete"] is not None and not rec["score"]["pass"]


def test_clarification_and_cache_invalidates():
    text = [ev("message_start", message=NS(usage=NS(input_tokens=5, cache_creation_input_tokens=100, cache_read_input_tokens=0))),
            ev("content_block_start", index=0, content_block=NS(type="text")),
            ev("content_block_delta", index=0, delta=NS(type="text_delta", text="Which channel do you mean?")),
            ev("content_block_stop", index=0), ev("message_delta", delta=NS(stop_reason="end_turn"))]
    rec = runner.run_trial(FakeClient(text), "claude-haiku-5-5", "medium", CASES["O1"], TOOLS, DATA["system"])
    assert rec["outcome"] == "clarification" and rec["t_tool_complete"] is None and rec["invalid"] == "cache_used"


def test_errors_are_recorded_not_retried():
    class Boom:
        messages = NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("429")))
    rec = runner.run_trial(Boom(), "claude-haiku-5-5", "medium", CASES["O1"], TOOLS, DATA["system"])
    assert rec["outcome"] == "error" and "429" in rec["error"]


def test_budget_guard_stops_the_run(tmp_path):
    c = FakeClient(tool_events("mcp__slack__slack_send_message", GOOD))
    c.stream.events = tool_events("mcp__slack__slack_send_message", GOOD)
    out = tmp_path / "r.jsonl"
    logs = []
    runner.run_all(c, ["claude-fable-5-1"], ["medium"], [CASES["O1"]], 5, 0, out, 0.0001, TOOLS, DATA["system"], log=logs.append)
    assert len(report.load(out)) == 1  # the first trial costs more than the limit, so the guard stops the second
    assert any("budget guard" in l for l in logs)


def test_plan_is_seeded_and_report_renders(tmp_path):
    a = runner.plan_trials(["m1", "m2"], ["low", "high"], list(CASES.values())[:3], 2, 7)
    assert a == runner.plan_trials(["m1", "m2"], ["low", "high"], list(CASES.values())[:3], 2, 7)
    c = FakeClient(tool_events("mcp__slack__slack_send_message", {"channel_id": "C05ARC9Z1", "message": "v2.14 deploy finished"}))
    rec = runner.run_trial(c, "claude-haiku-5-5", "medium", CASES["O1"], TOOLS, DATA["system"])
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps(rec) + "\n")
    report.render_html(report.load(p), tmp_path / "r.html")
    page = (tmp_path / "r.html").read_text()
    assert 'class=bad' in page and 'class=ok' in page and "C05ARC9Z1" in page
    assert "BAD" in report.format_terminal(rec, 0.01, 1, 1)


def test_every_tool_schema_is_valid_json_schema_2020_12():
    from jsonschema import Draft202012Validator
    for t in TOOLS:
        Draft202012Validator.check_schema(t["input_schema"])


def test_resume_runs_only_the_missing_trials():
    cases = [CASES["O1"], CASES["V1"]]
    plan = runner.plan_trials(["m"], ["low"], cases, 2, 0)
    done = [{"model": "m", "effort": "low", "case": "O1", "outcome": "tool_call"},
            {"model": "m", "effort": "low", "case": "O1", "outcome": "tool_call"},
            {"model": "m", "effort": "low", "case": "V1", "outcome": "tool_call"},
            {"model": "m", "effort": "low", "case": "V1", "outcome": "error"}]
    left = runner.remaining_trials(plan, done)
    assert [c["id"] for _, _, c in left] == ["V1"]


def test_pacing_waits_until_the_window_clears():
    from collections import deque
    now = [0.0]
    sleeps = []
    def sleep(s):
        sleeps.append(s)
        now[0] += s
    recent = deque([(0.0, 46000)] * 9)  # 414k tokens in the last minute, limit 500k -> 450k usable
    runner.pace(recent, 500000, 46000, sleep=sleep, clock=lambda: now[0])
    assert sleeps and now[0] >= 60  # had to wait for the old requests to leave the 60 s window
