"""A tiny reference implementation of the candidate v1 HTTP contract.

Run it, then point a spec at `http://127.0.0.1:8765` with `auth: none`:

    python examples/http_candidate_server.py
    python -m evalharness run examples/specs/http-candidate.yaml

Pass `--token <secret>` to require `Authorization: Bearer <secret>` and demo the
harness-owned credential path with examples/specs/http-candidate-bearer.yaml.
The answers are a scripted table; this process is a stand-in for a real agent.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence

SCRIPTED: dict[str, dict[str, Any]] = {
    "case-1": {
        "answer": "42",
        "citations": ["policy-1"],
        "usage": {"prompt_tokens": 812, "completion_tokens": 96},
        "tool_calls": 0,
        "artifacts": {"trace_id": "trace-case-1", "engine": "reference-stub"},
        "model": "reference-agent-v1",
    },
    "case-2": {
        "answer": "42",
        "citations": ["policy-2"],
        "usage": {"prompt_tokens": 804, "completion_tokens": 88},
        "tool_calls": 0,
        "artifacts": {"trace_id": "trace-case-2", "engine": "reference-stub"},
        "model": "reference-agent-v1",
    },
}
"""The scripted answers this reference server returns, keyed by case id."""


def answer_for(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the scripted response for one request body."""
    case_id = str(payload.get("case_id", ""))
    scripted = SCRIPTED.get(case_id)
    if scripted is not None:
        return scripted
    return {
        "answer": f"No scripted answer for {case_id!r}.",
        "citations": [],
        "tool_calls": 0,
        "artifacts": {"engine": "reference-stub"},
        "model": "reference-agent-v1",
    }


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str | None = None,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it; tests pass port 0."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/run":
                self._send(404, {"error": f"unknown path {self.path}"})
                return
            if token is not None:
                header = self.headers.get("Authorization", "")
                if header != f"Bearer {token}":
                    self._send(401, {"error": "invalid or missing bearer token"})
                    return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                self._send(400, {"error": "request body is not JSON"})
                return
            if not isinstance(payload, dict):
                self._send(400, {"error": "request body must be a JSON object"})
                return
            self._send(200, answer_for(payload))

        def _send(self, status: int, body: object) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, serve forever, and return an exit code."""
    parser = argparse.ArgumentParser(
        description="Reference HTTP candidate for the eval-harness v1 contract."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--token",
        help="require Authorization: Bearer <token>; omit for auth: none",
    )
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, args.token)
    print(
        f"candidate v1 reference server on "
        f"http://{args.host}:{server.server_address[1]}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
