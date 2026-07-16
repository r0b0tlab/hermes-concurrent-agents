"""Generic Linux integration against a real deterministic HTTP endpoint."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hca.backends.openai_compat import probe_chat, probe_models, probe_tools


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002
        del format, args

    def _send(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/v1/models":
            self._send({"object": "list", "data": [{"id": "fake-model"}]})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._send({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        assert request["model"] == "fake-model"
        assert request["messages"]
        message: dict = {"role": "assistant", "content": "HCA_OK"}
        if request.get("tools"):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_fake",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"text":"ping"}'},
                    }
                ],
            }
        self._send(
            {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            }
        )


@contextmanager
def fake_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_generic_fake_endpoint_models_chat_and_tools():
    with fake_endpoint() as endpoint:
        models = probe_models(endpoint, "fake-model", timeout=2)
        chat = probe_chat(endpoint, "fake-model", timeout=2)
        tools = probe_tools(endpoint, "fake-model", timeout=2)

    assert models.ok, models.detail
    assert chat.ok and "HCA_OK" in chat.detail
    assert tools.ok and "1 call" in tools.detail
