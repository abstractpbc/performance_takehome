"""Asyncio HTTP/1.1 server that speaks the OpenAI chat-completions SSE protocol.

Endpoints:
  GET  /v1/models            readiness (200 once ``models_ready_after_s`` has elapsed)
  POST /v1/chat/completions  streaming completion with the profile's injected faults
  GET  /_mock/oracle         ground truth for every request served so far
  GET  /_mock/profile        the active profile
  POST /_mock/shutdown       stop the server
The client identifies requests with the ``X-Bench-Request-Id`` header; otherwise a
counter is used. Everything is seeded: (seed, request_id) fully determines the stream.
"""

import argparse
import asyncio
import hashlib
import json
import random
import signal
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.tokens import count_tokens  # noqa: E402

from mock_server.faults import FaultProfile, StreamPlan, plan_stream, profile_by_name  # noqa: E402

DEFAULT_MODEL_ID = "mock/llm-d-stand-in"


def _rng_for(seed: int, request_id: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}|{request_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class MockServer:
    """One mock engine instance with a fixed fault profile and seed."""

    def __init__(
        self,
        profile: FaultProfile,
        seed: int,
        model_id: str = DEFAULT_MODEL_ID,
        host: str = "127.0.0.1",
        port: int = 0,
        time_scale: float = 1.0,
    ) -> None:
        self.profile = profile
        self.seed = seed
        self.model_id = model_id
        self.host = host
        self.port = port
        self.time_scale = time_scale
        self.oracle: dict[str, dict[str, Any]] = {}
        self.started_mono = time.monotonic()
        self._counter = 0
        self._server: asyncio.base_events.Server | None = None
        self._stop = asyncio.Event()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def models_ready(self) -> bool:
        return (
            time.monotonic() - self.started_mono
            >= self.profile.models_ready_after_s * self.time_scale
        )

    def inference_ready(self) -> bool:
        return (
            time.monotonic() - self.started_mono
            >= self.profile.inference_ready_after_s * self.time_scale
        )

    async def start(self) -> None:
        """Bind the socket; ``port`` is updated when 0 was requested."""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        self.started_mono = time.monotonic()

    def request_stop(self) -> None:
        """Ask ``serve_until_shutdown`` to return (used by SIGTERM and /_mock/shutdown)."""
        self._stop.set()

    async def serve_until_shutdown(self) -> None:
        await self._stop.wait()
        await self.close()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _read_request(
        self, reader: asyncio.StreamReader
    ) -> tuple[str, str, dict[str, str], bytes] | None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return None
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        body = b""
        length = int(headers.get("content-length", "0") or 0)
        if length:
            body = await reader.readexactly(length)
        return parts[0], parts[1], headers, body

    @staticmethod
    def _response(status: int, body: bytes, content_type: str = "application/json") -> bytes:
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }.get(status, "OK")
        return (
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode() + body

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            parsed = await self._read_request(reader)
            if parsed is None:
                return
            method, path, headers, body = parsed
            if method == "GET" and path == "/v1/models":
                await self._models(writer)
            elif method == "POST" and path == "/v1/chat/completions":
                await self._completion(writer, headers, body)
            elif method == "GET" and path == "/_mock/oracle":
                writer.write(
                    self._response(200, json.dumps(self.oracle, ensure_ascii=False).encode())
                )
            elif method == "GET" and path == "/_mock/profile":
                writer.write(
                    self._response(
                        200,
                        json.dumps(
                            {
                                "profile": self.profile.to_dict(),
                                "seed": self.seed,
                                "model_id": self.model_id,
                            }
                        ).encode(),
                    )
                )
            elif method == "POST" and path == "/_mock/shutdown":
                writer.write(self._response(200, b"{}"))
                self._stop.set()
            else:
                writer.write(self._response(404, b'{"error":"not found"}'))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    async def _models(self, writer: asyncio.StreamWriter) -> None:
        if not self.models_ready():
            writer.write(self._response(503, b'{"error":"loading"}'))
            return
        payload = {
            "object": "list",
            "data": [{"id": self.model_id, "object": "model", "owned_by": "mock"}],
        }
        writer.write(self._response(200, json.dumps(payload).encode()))

    async def _completion(
        self, writer: asyncio.StreamWriter, headers: dict[str, str], body: bytes
    ) -> None:
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            writer.write(self._response(400, b'{"error":"invalid json"}'))
            return
        if not self.inference_ready():
            writer.write(self._response(503, b'{"error":"model not ready"}'))
            return
        self._counter += 1
        request_id = headers.get("x-bench-request-id") or f"anon-{self._counter}"
        prompt_tokens = sum(
            count_tokens(str(m.get("content", ""))) for m in request.get("messages", [])
        )
        plan: StreamPlan = plan_stream(
            request_id,
            request,
            self.profile,
            _rng_for(self.seed, request_id),
            self.model_id,
            prompt_tokens,
            request_index=self._counter,
        )
        self.oracle[request_id] = plan.oracle
        if plan.http_status != 200:
            writer.write(
                self._response(
                    plan.http_status, json.dumps({"error": {"message": "injected"}}).encode()
                )
            )
            return
        if not request.get("stream", False):
            writer.write(self._response(400, b'{"error":"mock supports stream=true only"}'))
            return
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
            b"Connection: close\r\nX-Mock-Profile: " + self.profile.name.encode() + b"\r\n\r\n"
        )
        await writer.drain()
        server_times: list[float] = []
        for index, chunk in enumerate(plan.chunks):
            if chunk.delay_s > 0:
                await asyncio.sleep(chunk.delay_s * self.time_scale)
            writer.write(f"data: {chunk.payload}\n\n".encode())
            await writer.drain()
            server_times.append(time.monotonic())
            if plan.drop_after_chunk is not None and index == plan.drop_after_chunk:
                break
        plan.oracle["server_chunk_mono_s"] = server_times


async def serve(
    profile_name: str,
    seed: int,
    port: int,
    model_id: str = DEFAULT_MODEL_ID,
    time_scale: float = 1.0,
    host: str = "127.0.0.1",
    oracle_dump: Path | None = None,
) -> None:
    """Run one mock server until ``/_mock/shutdown`` or SIGTERM; optionally dump the oracle."""
    server = MockServer(profile_by_name(profile_name), seed, model_id, host, port, time_scale)
    await server.start()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, server.request_stop)
    print(
        json.dumps({"base_url": server.base_url, "profile": profile_name, "seed": seed}), flush=True
    )
    await server.serve_until_shutdown()
    if oracle_dump is not None:
        oracle_dump.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profile": server.profile.to_dict(), "seed": seed, "requests": server.oracle}
        oracle_dump.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile", default="clean")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port (printed as JSON)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="multiply every injected delay (use <1 to run fixtures faster)",
    )
    parser.add_argument("--oracle-dump", type=Path, help="write the oracle JSON here on shutdown")
    args = parser.parse_args(argv)
    asyncio.run(
        serve(
            args.profile,
            args.seed,
            args.port,
            args.model_id,
            args.time_scale,
            args.host,
            args.oracle_dump,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
