"""A reference stub for the two supported provider families.

Run it, then point a spec at `http://127.0.0.1:8770` with `auth: none`:

    python examples/provider_server.py
    python -m evalharness run examples/specs/providers.yaml

It answers `POST /chat/completions` in the OpenAI-compatible shape and
`POST /v1/messages` in the Anthropic shape. The content is the documented JSON
verdict, so the same stub serves a provider candidate (as its answer) and a
provider judge (as its parsed verdict). Usage is reported in each dialect,
which is what makes a run record `usage_source=provider`.

Pass `--openai-token` and `--anthropic-token` to require the two auth shapes:
`Authorization: Bearer <token>` and `x-api-key: <token>` respectively. The
reference server is a stand-in; nothing here calls a paid provider.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Sequence

VERDICT = '{"passed": true, "reason": "reference stub verdict", "score": 1.0}'
"""The reply content: a valid answer and a parseable judge verdict."""

OPENAI_USAGE = {"prompt_tokens": 11, "completion_tokens": 5}
ANTHROPIC_USAGE = {"input_tokens": 11, "output_tokens": 5}


def openai_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Return one OpenAI-compatible chat-completions response."""
    return {
        "model": str(payload.get("model") or "stub-openai"),
        "choices": [
            {"message": {"role": "assistant", "content": VERDICT}, "finish_reason": "stop"}
        ],
        "usage": dict(OPENAI_USAGE),
    }


def anthropic_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Return one Anthropic-style messages response."""
    return {
        "model": str(payload.get("model") or "stub-anthropic"),
        "content": [{"type": "text", "text": VERDICT}],
        "stop_reason": "end_turn",
        "usage": dict(ANTHROPIC_USAGE),
    }


def create_server(
    host: str = "127.0.0.1",
    port: int = 8770,
    openai_token: str | None = None,
    anthropic_token: str | None = None,
) -> ThreadingHTTPServer:
    """Build the provider stub without starting it; tests pass port 0."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path == "/chat/completions":
                if openai_token is not None:
                    expected = f"Bearer {openai_token}"
                    if self.headers.get("Authorization", "") != expected:
                        self._send(
                            401, {"error": "invalid or missing bearer token"}
                        )
                        return
                self._send(200, openai_body(self._payload()))
                return
            if self.path == "/v1/messages":
                if (
                    anthropic_token is not None
                    and self.headers.get("x-api-key", "") != anthropic_token
                ):
                    self._send(
                        401,
                        {
                            "type": "error",
                            "error": {
                                "type": "authentication_error",
                                "message": "invalid x-api-key",
                            },
                        },
                    )
                    return
                self._send(200, anthropic_body(self._payload()))
                return
            self._send(404, {"error": f"unknown path {self.path}"})

        def _payload(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                return {}
            return payload if isinstance(payload, dict) else {}

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
        description="Reference provider stub for the OpenAI-compatible and "
        "Anthropic-style adapters."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument(
        "--openai-token",
        help="require Authorization: Bearer <token> on /chat/completions",
    )
    parser.add_argument(
        "--anthropic-token",
        help="require x-api-key: <token> on /v1/messages",
    )
    args = parser.parse_args(argv)
    server = create_server(
        args.host, args.port, args.openai_token, args.anthropic_token
    )
    print(
        f"provider reference stub on "
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
