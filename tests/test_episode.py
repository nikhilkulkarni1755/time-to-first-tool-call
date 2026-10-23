"""Episodes (time to first correct tool call), driven by scripted model turns. No network, no spend."""
import copy
import json
from types import SimpleNamespace as NS

from tests.test_ttftc import CASES, DATA, TOOLS, FakeStream, ev
from ttftc import connectors, episode, report, runner, scoring

GOOD = {"channel_id": "C07RLS4K2", "message": "v2.14 deploy finished"}
SLACK = "mcp__slack__slack_send_message"


def turn(*calls, text=None, thinking=False, stop="tool_use", sig="sigA"):
    """Events of one assistant message: optional thinking, optional text, then tool_use blocks (name, input, id)."""
    out = [ev("message_start", message=NS(usage=NS(input_tokens=46000, cache_creation_input_tokens=0, cache_read_input_tokens=0)))]
    i = 0
    if thinking:
        out += [ev("content_block_start", index=i, content_block=NS(type="thinking")),
                ev("content_block_delta", index=i, delta=NS(type="thinking_delta", thinking="plan")),
                ev("content_block_delta", index=i, delta=NS(type="signature_delta", signature=sig)),
                ev("content_block_stop", index=i)]
        i += 1
    if text is not None:
        out += [ev("content_block_start", index=i, content_block=NS(type="text")),
                ev("content_block_delta", index=i, delta=NS(type="text_delta", text=text)), ev("content_block_stop", index=i)]
        i += 1
    for name, payload, tid in calls:
        s = json.dumps(payload)
        out += [ev("content_block_start", index=i, content_block=NS(type="tool_use", name=name, id=tid)),
                ev("content_block_delta", index=i, delta=NS(type="input_json_delta", partial_json=s[:7])),
                ev("content_block_delta", index=i, delta=NS(type="input_json_delta", partial_json=s[7:])),
                ev("content_block_stop", index=i)]
        i += 1
    out += [ev("message_delta", delta=NS(stop_reason=stop), usage=NS(output_tokens=120))]
    return out


class Script:
    """Client whose n-th request replays the n-th scripted turn; records what was sent."""
    def __init__(self, *turns):
        self.turns, self.sent, self.streams = list(turns), [], []
        self.messages = NS(create=self.create)

    def create(self, **kw):
        self.sent.append(copy.deepcopy(kw))
        s = FakeStream(self.turns[len(self.sent) - 1])
        self.streams.append(s)
        return s


def run(client, case="O1", **kw):
    return episode.run_episode(client, "claude-haiku-5-5", "medium", CASES[case], TOOLS, DATA["system"], **kw)


def test_first_call_green_is_ttfctc_equals_ttftc_and_aborts():
    c = Script(turn((SLACK, GOOD, "t1"), text="trailing"))
    ep = run(c)
    assert ep["outcome"] == "solved" and ep["calls_before_correct"] == 0 and ep["turns_to_correct"] == 1
    assert ep["t_correct"] == ep["t_first_call"] and c.streams[0].closed and len(c.sent) == 1
    assert c.streams[0].consumed < len(c.streams[0].events)


def test_lookup_then_correct_counts_one_wrong_call_and_round_trips_thinking():
    lookup = ("mcp__slack__slack_search_channels", {"query": "releases"}, "t1")
    c = Script(turn(lookup, thinking=True, text="let me look"), turn((SLACK, GOOD, "t2")))
    ep = run(c)
    assert ep["outcome"] == "solved" and ep["calls_before_correct"] == 1 and ep["turns_to_correct"] == 2
    assert ep["wrong_writes"] == 0 and ep["calls"][0]["read_only"] and not ep["calls"][0]["ok"]
    assert ep["t_first_call"] < ep["t_correct"]
    second = c.sent[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    kinds = [b["type"] for b in second[1]["content"]]
    assert kinds == ["thinking", "text", "tool_use"] and second[1]["content"][0]["signature"] == "sigA"
    res = second[2]["content"][0]
    assert res["type"] == "tool_result" and res["tool_use_id"] == "t1" and json.loads(res["content"]) == {"results": [], "total_count": 0}
    assert "cache_control" not in json.dumps(c.sent[1]) and c.sent[1]["output_config"] == {"effort": "medium"}


def test_wrong_write_is_counted_and_gets_a_success_result():
    wrong = ("mcp__slack__slack_send_message", {"channel_id": "C05ARC9Z1", "message": "v2.14 deploy finished"}, "t1")
    c = Script(turn(wrong), turn((SLACK, GOOD, "t2")))
    ep = run(c)
    assert ep["outcome"] == "solved" and ep["wrong_writes"] == 1 and ep["calls_before_correct"] == 1
    assert json.loads(c.sent[1]["messages"][2]["content"][0]["content"]) == {"ok": True}


def test_parallel_calls_each_get_a_result_and_correct_one_in_the_same_turn_wins():
    lookup = ("mcp__slack__slack_search_channels", {"query": "releases"}, "t1")
    c = Script(turn(lookup, (SLACK, GOOD, "t2")))
    ep = run(c)
    assert ep["outcome"] == "solved" and ep["calls_before_correct"] == 1 and ep["turns_to_correct"] == 1 and len(c.sent) == 1
    both = Script(turn(lookup, ("mcp__slack__slack_read_channel", {"channel_id": "C07RLS4K2"}, "t2")), turn((SLACK, GOOD, "t3")))
    ep = run(both)
    results = both.sent[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and ep["calls_before_correct"] == 2


def test_gives_up_clarifies_and_hits_the_turn_cap():
    assert run(Script(turn(text="Done.", stop="end_turn")))["outcome"] == "gave_up"
    assert run(Script(turn(text="Which channel?", stop="end_turn")))["outcome"] == "clarification"
    look = ("mcp__slack__slack_search_channels", {"query": "x"}, "t")
    ep = run(Script(turn(look), turn(look), turn(look)), max_turns=3)
    assert ep["outcome"] == "max_turns" and ep["t_correct"] is None and len(ep["calls"]) == 3
    # unsolved after a wrong write that the mock accepted
    wrong = ("mcp__slack__slack_send_message", {"channel_id": "C05ARC9Z1", "message": "x"}, "t1")
    ep = run(Script(turn(wrong), turn(text="Done.", stop="end_turn")))
    assert ep["outcome"] == "gave_up" and ep["wrong_writes"] == 1


def test_bad_json_is_a_red_call_with_an_error_result():
    events = turn((SLACK, GOOD, "t1"))
    for e in events:  # break the JSON
        if getattr(e, "delta", None) is not None and getattr(e.delta, "type", "") == "input_json_delta" and not e.delta.partial_json.startswith("{"):
            e.delta.partial_json = "oops"
    c = Script(events, turn((SLACK, GOOD, "t2")))
    ep = run(c)
    assert ep["calls"][0]["bad_json"] and not ep["calls"][0]["ok"] and ep["outcome"] == "solved"
    assert c.sent[1]["messages"][2]["content"][0]["is_error"] is True


def test_clock_excludes_mock_execution_and_gate_waits():
    now = [0.0]
    def clock():
        now[0] += 0.01  # every reading advances time
        return now[0]
    waits = []
    def gate():
        waits.append(1)
        now[0] += 100  # a long pacing wait
    look = ("mcp__slack__slack_search_channels", {"query": "x"}, "t1")
    ep = run(Script(turn(look), turn((SLACK, GOOD, "t2"))), clock=clock, gate=gate)
    assert len(waits) == 2 and ep["outcome"] == "solved" and ep["t_correct"] < 5  # 200 s of gate wait is not counted


def test_cache_use_invalidates_and_errors_are_recorded():
    events = turn((SLACK, GOOD, "t1"))
    events[0].message.usage.cache_read_input_tokens = 5
    assert run(Script(events))["invalid"] == "cache_used"
    class Boom:
        messages = NS(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("429")))
    ep = run(Boom())
    assert ep["outcome"] == "error" and "429" in ep["error"]


def test_cost_sums_every_turn():
    look = ("mcp__slack__slack_search_channels", {"query": "x"}, "t1")
    ep = run(Script(turn(look), turn((SLACK, GOOD, "t2"))))
    assert ep["cost_est"] > 2 * runner.cost("claude-haiku-5-5", 46000, 0) * 0.99 and len(ep["turns"]) == 2


def test_read_only_classification_of_every_tool():
    reads = sorted(connectors.logical(t["name"]) for t in TOOLS if episode.is_read_only(connectors.logical(t["name"])))
    writes = sorted(connectors.logical(t["name"]) for t in TOOLS if not episode.is_read_only(connectors.logical(t["name"])))
    assert len(reads) + len(writes) == 92
    for name in ("github.issue_read", "github.pull_request_read", "notion.notion-fetch", "notion.notion-search", "notion.notion-get-self",
                 "slack.slack_read_channel", "slack.slack_search_users", "slack.slack_get_reactions", "linear.search_documentation",
                 "cloudflare.get_zone_setting", "cloudflare.list_dns_records", "github.get_file_contents", "notion.notion-get-async-task"):
        assert name in reads, name
    for name in ("slack.slack_send_message", "slack.slack_add_reaction", "slack.slack_send_message_draft", "slack.slack_schedule_message",
                 "github.issue_write", "github.add_issue_comment", "github.create_or_update_file", "cloudflare.purge_cache",
                 "cloudflare.query_d1_database", "linear.save_issue", "notion.notion-update-page", "notion.notion-create-comment",
                 "notion.notion-create-attachment", "notion.notion-create-file-upload", "notion.notion-move-pages"):
        assert name in writes, name


def test_every_lookup_fixture_names_a_real_read_only_tool_and_fills_from_input():
    names = {connectors.logical(t["name"]) for t in TOOLS}
    for tool in episode.FIXTURES:
        assert tool in names and episode.is_read_only(tool), tool
    assert json.loads(episode.mock_result("github.issue_read", {"issue_number": 482}))["number"] == 482 or \
        json.loads(episode.mock_result("github.issue_read", {"issue_number": 482}))["number"] == "482"
    assert json.loads(episode.mock_result("cloudflare.get_zone", {"zone_id": "z"}))["id"] == "z"
    assert json.loads(episode.mock_result("notion.notion-fetch", {}))["id"] == ""  # missing input -> empty, never a crash


def test_a_correct_answer_exists_for_every_case_and_branch():
    """Every accepted branch of every case, filled with its own literal values, scores green and is solvable in an episode."""
    sample = {
        "O1": GOOD, "O2": {"owner": "acme-labs", "repo": "billing-api", "issue_number": 482, "body": "Fixed in #517"},
    }
    for cid, payload in sample.items():
        tool = "mcp__" + CASES[cid]["accept"][0]["tool"].replace(".", "__")
        ep = run(Script(turn((tool, payload, "t"))), case=cid)
        assert ep["outcome"] == "solved", cid


def test_run_all_episodes_report_resume_and_budget(tmp_path):
    scripts = [turn((SLACK, GOOD, "t1"))]
    class Many(Script):
        def create(self, **kw):
            self.sent.append(kw)
            s = FakeStream(scripts[0])
            self.streams.append(s)
            return s
    out, logs = tmp_path / "e.jsonl", []
    episode.run_all_episodes(Many(), ["claude-haiku-5-5"], ["low", "high"], [CASES["O1"]], 2, 0, out, 5.0, TOOLS, DATA["system"], log=logs.append)
    rows = report.load(out)
    assert len(rows) == 4 and all(r["outcome"] == "solved" for r in rows)
    report.render_episode_html(rows, tmp_path / "e.html")
    page = (tmp_path / "e.html").read_text()
    assert "TTFCTC" in page and "class=ok" in page
    s = report.summarize_episodes(rows)
    assert {x["effort"] for x in s} == {"low", "high"} and all(x["solved"] == 2 and x["first_try"] == 2 for x in s)
    assert "SOLVED" in report.format_episode_terminal(rows[0], 0.1, 1, 4)
    logs.clear()
    episode.run_all_episodes(Many(), ["claude-haiku-5-5"], ["low", "high"], [CASES["O1"]], 3, 0, tmp_path / "e2.jsonl", 5.0, TOOLS,
                             DATA["system"], log=logs.append, resume_rows=rows)
    assert any("resume: 2 episodes" in l for l in logs)
    tiny = tmp_path / "b.jsonl"
    episode.run_all_episodes(Many(), ["claude-fable-5-1"], ["low"], [CASES["O1"]], 5, 0, tiny, 0.0001, TOOLS, DATA["system"], log=logs.append)
    assert len(report.load(tiny)) == 1 and any("budget guard" in l for l in logs)


def test_real_sdk_two_turn_episode_against_a_local_sse_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import anthropic

    def sse(event, data):
        return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()

    def start(i, block):
        return ("content_block_start", {"type": "content_block_start", "index": i, "content_block": block})

    def delta(i, d):
        return ("content_block_delta", {"type": "content_block_delta", "index": i, "delta": d})

    def stop(i):
        return ("content_block_stop", {"type": "content_block_stop", "index": i})

    usage = {"input_tokens": 46000, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    head = ("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                                                    "model": "claude-haiku-5-5", "stop_reason": None, "stop_sequence": None, "usage": usage}})
    tail = [("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 80}}),
            ("message_stop", {"type": "message_stop"})]
    look = json.dumps({"query": "releases"})
    turn1 = [head, start(0, {"type": "thinking", "thinking": "", "signature": ""}),
             delta(0, {"type": "thinking_delta", "thinking": "find the channel"}), delta(0, {"type": "signature_delta", "signature": "SIG1"}), stop(0),
             start(1, {"type": "text", "text": ""}), delta(1, {"type": "text_delta", "text": "Looking."}), stop(1),
             start(2, {"type": "tool_use", "id": "toolu_A", "name": "mcp__slack__slack_search_channels", "input": {}}),
             delta(2, {"type": "input_json_delta", "partial_json": look[:9]}), delta(2, {"type": "input_json_delta", "partial_json": look[9:]}), stop(2), *tail]
    good = json.dumps(GOOD)
    turn2 = [head, start(0, {"type": "tool_use", "id": "toolu_B", "name": SLACK, "input": {}}),
             delta(0, {"type": "input_json_delta", "partial_json": good[:12]}), delta(0, {"type": "input_json_delta", "partial_json": good[12:]}),
             stop(0), *tail]
    bodies = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            bodies.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            try:
                for name, data in (turn1, turn2)[len(bodies) - 1]:
                    self.wfile.write(sse(name, data))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = anthropic.Anthropic(api_key="sk-local", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0)
    ep = run(client)
    server.shutdown()
    assert ep["outcome"] == "solved", ep["error"]
    assert ep["calls_before_correct"] == 1 and ep["turns_to_correct"] == 2 and ep["wrong_writes"] == 0
    assert len(bodies) == 2 and all(b["stream"] and "temperature" not in b and len(b["tools"]) == 92 for b in bodies)
    sent = bodies[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"][0] == {"type": "thinking", "thinking": "find the channel", "signature": "SIG1"}
    assert sent[1]["content"][1] == {"type": "text", "text": "Looking."}
    assert sent[1]["content"][2] == {"type": "tool_use", "id": "toolu_A", "name": "mcp__slack__slack_search_channels", "input": {"query": "releases"}}
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_A" and sent[2]["content"][0]["type"] == "tool_result"


def test_wrong_tool_calls_show_no_parameter_grid_and_bad_json_renders(tmp_path):
    lookup = ("mcp__notion__notion-fetch", {"id": "x"}, "t1")
    events = turn((SLACK, GOOD, "t2"))
    for e in events:
        if getattr(e, "delta", None) is not None and getattr(e.delta, "type", "") == "input_json_delta" and not e.delta.partial_json.startswith("{"):
            e.delta.partial_json = "oops"
    ep = run(Script(turn(lookup), events, turn((SLACK, GOOD, "t3"))))
    assert ep["calls"][1]["bad_json"] and ep["outcome"] == "solved"
    text = report.format_episode_terminal(ep, 0.0, 1, 1)
    assert "notion-fetch" in text and "want" not in text  # no grid for the wrong tool or the unparsable call
    report.render_episode_html([ep], tmp_path / "e.html")


def test_loosened_keys_accept_harmless_extras_but_not_invented_fields():
    lin = {"team": "PLT", "title": "Users get logged out after 5 minutes", "description": "details"}
    assert scoring.score(CASES["A1"], "linear.save_issue", lin)["pass"]
    assert not scoring.score(CASES["A1"], "linear.save_issue", {**lin, "priority": 2})["pass"]
    assert scoring.score(CASES["A4"], "slack.slack_send_message", {"channel_id": "C03ENGQ77", "message": "checkout bug fixed", "unfurl_app_links": True})["pass"]
    assert scoring.score(CASES["V4"], "notion.notion-search", {"query": "on-call", "teamspace_id": "3f8a2c60-9d14-4b7e-a5c3-0e1f6b9d2a47", "page_size": 10})["pass"]
