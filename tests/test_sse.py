"""The real Anthropic SDK against a local server that speaks the Messages SSE protocol (no network, no spend)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import anthropic

from ttftc import connectors, runner

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "cases.json").read_text())
CASE = next(c for c in DATA["cases"] if c["id"] == "O2")
INPUT = json.dumps({"owner": "acme-labs", "repo": "billing-api", "issue_number": 482, "body": "Fixed in #517"})


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        Handler.seen["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("request-id", "req_local")
        self.end_headers()
        usage = {"input_tokens": 31000, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        events = [
            ("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                                                    "model": "claude-haiku-5-5", "stop_reason": None, "stop_sequence": None, "usage": usage}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Need issue 482."}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1,
                                     "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mcp__github__add_issue_comment", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": INPUT[:20]}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": INPUT[20:]}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ]
        try:
            for name, data in events:
                self.wfile.write(sse(name, data))
                self.wfile.flush()
                time.sleep(0.05)
            Handler.seen["sent_everything_before_close"] = False
            time.sleep(1.0)  # a long tail the client should not wait for
            self.wfile.write(sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None}, "usage": {"output_tokens": 50}}))
            self.wfile.write(sse("message_stop", {"type": "message_stop"}))
            Handler.seen["sent_everything_before_close"] = True
        except (BrokenPipeError, ConnectionResetError):
            Handler.seen["client_closed_early"] = True


def serve():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_real_sdk_stream_is_timed_scored_and_aborted_early():
    server = serve()
    client = anthropic.Anthropic(api_key="sk-local", base_url=f"http://127.0.0.1:{server.server_port}", max_retries=0)
    started = time.monotonic()
    rec = runner.run_trial(client, "claude-haiku-5-5", "medium", CASE, connectors.load_tools(), DATA["system"])
    wall = time.monotonic() - started
    server.shutdown()
    assert rec["outcome"] == "tool_call", rec["error"]
    assert rec["score"]["pass"] and rec["tool"] == "mcp__github__add_issue_comment"
    assert rec["usage"]["input_tokens"] == 31000 and rec["request_id"] == "req_local"
    assert 0.15 <= rec["t_tool_complete"] < 0.6  # events are 50 ms apart; the 1 s tail must not be waited for
    assert wall < 0.9 and rec["aborted"]
    assert rec["t_thinking_start"] is not None and rec["t_tool_start"] < rec["t_tool_complete"]
    body = Handler.seen["body"]
    assert body["stream"] is True and body["output_config"] == {"effort": "medium"} and len(body["tools"]) == 92
    assert "cache_control" not in json.dumps(body) and "temperature" not in body
