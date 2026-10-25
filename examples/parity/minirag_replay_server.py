"""Replay the recorded minirag pipeline over the candidate-v1 contract.

This server is not a mock of the harness: it is the recorded RAG pipeline
answering the same requests the candidate endpoint answered when the parity
fixture was captured. Every answer, citation, token count and tool-call count
comes from `examples/parity/fixture/`, keyed by the request prompt and the
injected context. It needs no Postgres, no Ollama and no model.

Start it to poke the contract by hand:

    python examples/parity/minirag_replay_server.py

`tests/test_parity_minirag.py` starts it on an ephemeral port, builds the
three-candidate spec from the fixture, runs it through the CLI, and asserts the
committed scoreboard. The fixture's provenance, the recorded scoreboard and the
pinned sha256 of `expected.json` are in `examples/parity/fixture/README.md`.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

FIXTURE_DIR = Path(__file__).resolve().parent / "fixture"
"""The committed parity fixture this server replays."""

REFUSAL = "The provided policy does not answer this question."
"""The exact refusal the RAG pipeline returned when no excerpt supported an answer."""

MAP_CHUNK_PREFIX = "map-"
"""Context chunks with this id prefix are the composed map excerpt."""

CONTRACT = "candidate-v1"
"""The contract version every response speaks."""


def _load(fixture_dir: Path, name: str) -> Any:
    """Load one committed fixture file."""
    return json.loads((fixture_dir / name).read_text(encoding="utf-8"))


class RecordedPipeline:
    """The recorded three-arm pipeline, keyed by prompt and injected context.

    The recorded arms differed only in what the candidate could see: the bare
    arm had to retrieve, the map arm saw the map excerpt, and the map-plus-rules
    arm also saw the cited rule. This class encodes exactly that, and nothing
    about scoring, specs or prices.
    """

    def __init__(self, fixture_dir: Path = FIXTURE_DIR) -> None:
        """Read the fixture once; the server never mutates it."""
        self._tasks = {
            str(task["prompt"]): task for task in _load(fixture_dir, "tasks.json")
        }
        self._answers = _load(fixture_dir, "answers.json")
        self._retrieval = _load(fixture_dir, "retrieval.json")["queries"]
        self.model = str(self._answers["model"])

    def respond(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Return the recorded v1 response for one request body.

        Raises:
            KeyError: The fixture has no recorded task for this prompt.
        """
        prompt = str(payload.get("prompt", ""))
        task = self._tasks[prompt]
        context = [dict(chunk) for chunk in payload.get("context") or []]
        task_id = str(task["id"])
        expected_ids = {
            str(chunk_id)
            for chunk_id in (task.get("expected") or {}).get("chunk_ids") or []
        }
        injected = bool(context)
        if injected:
            chunks = context
            retrieval = "request"
            tool_calls = 0
        else:
            chunks = [
                dict(chunk)
                for chunk in self._retrieval.get(prompt, {}).get("chunks") or []
            ]
            retrieval = "retrieval"
            tool_calls = 1
        chunk_ids = [str(chunk.get("chunk_id", "")) for chunk in chunks]
        has_map = any(chunk_id.startswith(MAP_CHUNK_PREFIX) for chunk_id in chunk_ids)
        has_rules = bool(expected_ids.intersection(chunk_ids))
        # The recorded pipeline refused before calling the model only when the
        # generator had no excerpt at all; injected context always reached it.
        model_called = injected or bool(chunks)
        answerable = has_map if int(task["tier"]) == 1 else has_rules
        usage = (
            self._answers["usage"][task_id]
            if model_called
            else {"prompt_tokens": 0, "completion_tokens": 0}
        )
        return {
            "answer": str(self._answers["answers"][task_id]) if answerable else REFUSAL,
            "citations": [chunk_id for chunk_id in chunk_ids if chunk_id in expected_ids]
            if answerable
            else [],
            "usage": {
                "prompt_tokens": int(usage["prompt_tokens"]),
                "completion_tokens": int(usage["completion_tokens"]),
            },
            "tool_calls": tool_calls,
            "artifacts": {
                "case_id": str(payload.get("case_id", "")),
                "retrieval": retrieval,
                "chunk_ids": chunk_ids,
            },
            "model": self.model,
        }


def _bad_request(message: str) -> tuple[int, dict[str, Any]]:
    """Build a JSON error body for a request the recorded pipeline cannot answer."""
    return 400, {"error": message}


def respond_to(pipeline: RecordedPipeline, payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate one decoded request body and return its status and response."""
    if not isinstance(payload, Mapping):
        return _bad_request("request body must be a JSON object")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return _bad_request("request.prompt must be a non-empty string")
    context = payload.get("context", [])
    if not isinstance(context, list):
        return _bad_request("request.context must be a list of chunks")
    if not all(isinstance(chunk, Mapping) for chunk in context):
        return _bad_request("request.context must be a list of chunks")
    try:
        return 200, pipeline.respond(payload)
    except KeyError:
        return _bad_request(f"the fixture has no recorded task for {prompt!r}")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8769,
    fixture_dir: Path = FIXTURE_DIR,
) -> ThreadingHTTPServer:
    """Build the HTTP server without starting it; tests pass port 0."""
    pipeline = RecordedPipeline(fixture_dir)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/run":
                self._send(404, {"error": f"unknown path {self.path}"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._send(400, {"error": "Content-Length is not an integer"})
                return
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                self._send(400, {"error": "request body is not JSON"})
                return
            self._send(*respond_to(pipeline, payload))

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                self._send(200, {"status": "ok", "contract": CONTRACT})
                return
            self._send(404, {"error": f"unknown path {self.path}"})

        def _send(self, status: int, body: Mapping[str, Any]) -> None:
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
        description="Replay the recorded minirag pipeline over candidate-v1."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=FIXTURE_DIR,
        help="fixture directory; defaults to examples/parity/fixture",
    )
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, args.fixture)
    print(
        f"recorded minirag pipeline ({CONTRACT}) on "
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
