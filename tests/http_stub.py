"""A real local HTTP server that speaks the candidate v1 contract.

Tests drive the HTTP candidate against this server, never a mock transport.
The responder is a plain callable so a test can script status codes, usage and
the number of requests the server actually saw.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

Responder = Callable[[dict[str, Any]], tuple[int, object]]
"""Map one decoded request payload to (HTTP status, body)."""

PathResponder = Callable[[str, dict[str, Any]], tuple[int, object]]
"""Map one request path and payload to (HTTP status, body)."""

PROVIDER_VERDICT = '{"passed": true, "reason": "stub verdict", "score": 1.0}'
"""The reply a provider stub returns: a valid answer and a parseable verdict."""


def provider_response(path: str, payload: dict[str, Any]) -> tuple[int, object]:
    """Answer one provider request in the shape the path's dialect expects.

    The same content reads as an answer for a candidate and as the documented
    JSON verdict for a judge, so one stub exercises both kinds. Usage is
    reported by the provider in each dialect, which is what makes a run record
    `usage_source=provider`.
    """
    if path.endswith("/v1/messages"):
        return 200, {
            "content": [{"type": "text", "text": PROVIDER_VERDICT}],
            "usage": {"input_tokens": 11, "output_tokens": 5},
            "model": "stub-anthropic",
        }
    return 200, {
        "choices": [{"message": {"role": "assistant", "content": PROVIDER_VERDICT}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        "model": "stub-openai",
    }


def default_response(payload: dict[str, Any]) -> tuple[int, object]:
    """Answer the conformance case with provider usage and a correct answer."""
    case_id = str(payload.get("case_id", "case"))
    return 200, {
        "answer": "42",
        "citations": [f"chunk-{case_id}"],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        "tool_calls": 2,
        "artifacts": {"trace_id": f"trace-{case_id}"},
        "model": "stub-agent-v1",
    }


@dataclass
class RecordedRequest:
    """One request the stub server actually received."""

    path: str
    headers: dict[str, str]
    payload: dict[str, Any]


@dataclass
class CandidateStubServer:
    """A threaded loopback server whose responder and request log tests own."""

    responder: Responder = default_response
    path_responder: PathResponder | None = None
    requests: list[RecordedRequest] = field(default_factory=list)
    _server: ThreadingHTTPServer = field(init=False, repr=False)
    _thread: threading.Thread = field(init=False, repr=False)

    def __post_init__(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                outer.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers=dict(self.headers.items()),
                        payload=payload,
                    )
                )
                if outer.path_responder is not None:
                    status, body = outer.path_responder(self.path, payload)
                else:
                    status, body = outer.responder(payload)
                if isinstance(body, (dict, list)):
                    encoded = json.dumps(body).encode("utf-8")
                    content_type = "application/json"
                else:
                    encoded = str(body).encode("utf-8")
                    content_type = "text/plain"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    @property
    def base_url(self) -> str:
        """Return the loopback base URL with the ephemeral port."""
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        """Stop the server and join its thread."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
