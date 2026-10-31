"""Study-only loopback SSE proxy. Importing starts no server or engine."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DELAY_SECONDS = 0.100
MAX_BYTES = 2 << 20


def payload(frame):
    data = b"\n".join(
        line[5:].lstrip(b" ") for line in frame.splitlines() if line.startswith(b"data:")
    )
    if not data or data == b"[DONE]":
        return None
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def content(frame):
    value = payload(frame)
    if value is None:
        return ""
    return "".join(
        c.get("delta", {}).get("content", "")
        for c in value.get("choices", [])
        if isinstance(c.get("delta", {}).get("content", ""), str)
    )


def wrong_nonce_frames(frames, expected):
    """Buffer this diagnostic only; replace an actual echoed nonce across deltas."""
    text = "".join(content(frame) for frame in frames)
    if not expected or expected not in text:
        return frames, False
    replacement = ("Z" if expected[0] != "Z" else "Y") + expected[1:]
    transformed = text.replace(expected, replacement)
    position = 0
    result = []
    for frame in frames:
        value = payload(frame)
        if value is None:
            result.append(frame)
            continue
        for choice in value.get("choices", []):
            delta = choice.get("delta", {})
            current = delta.get("content")
            if isinstance(current, str):
                delta["content"] = transformed[position : position + len(current)]
                position += len(current)
        result.append(b"data: " + json.dumps(value, separators=(",", ":")).encode() + b"\n\n")
    return result, True


def make_server(upstream, evidence_root, *, port=0):
    url = urlsplit(upstream)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or url.path not in {"", "/"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("fixed loopback engine origin required")
    root = Path(evidence_root)
    root.mkdir(mode=0o700)
    active = set()
    active_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/v1/models":
                self.send_error(404)
                return
            connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=5)
            try:
                connection.request("GET", self.path)
                response = connection.getresponse()
                body = response.read(65537)
                if len(body) > 65536:
                    raise ValueError("bounded readiness")
                self.send_response(response.status)
                for key in ("Content-Type", "server", "x-mock-profile"):
                    if response.getheader(key):
                        self.send_header(key, response.getheader(key))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError):
                self.send_error(503)
            finally:
                connection.close()

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length <= 1 << 20:
                self.send_error(400)
                return
            request = self.rfile.read(length)
            body = json.loads(request)
            request_id = self.headers.get("X-Bench-Request-Id", "")
            arm = next(
                (
                    name
                    for name in ("delay100", "wrongnonce", "earlyclose")
                    if request_id.startswith(name + "|")
                ),
                "clean",
            )
            label = hashlib.sha256(request_id.encode()).hexdigest()[:20]
            directory = root / label
            directory.mkdir(mode=0o700)
            transcript = directory / "events.jsonl"
            events = transcript.open("x")
            event_lock = threading.Lock()
            frames = queue.Queue(maxsize=256)
            outcome = {
                "request_id": request_id,
                "arm": arm,
                "upstream_complete": False,
                "injection_applied": False,
                "upstream_time_semantics": "proxy receive boundary, not engine token emission",
            }

            def event(kind, **fields):
                with event_lock:
                    row = {"event": kind, "monotonic_ns": time.monotonic_ns(), **fields}
                    events.write(json.dumps(row) + "\n")
                    events.flush()
                    os.fsync(events.fileno())
                return row["monotonic_ns"]

            with active_lock:
                active.add(label)
            connection = http.client.HTTPConnection("127.0.0.1", url.port, timeout=30)
            response = None
            try:
                connection.request(
                    "POST", self.path, body=request, headers={"Content-Type": "application/json"}
                )
                response = connection.getresponse()
                outcome["HTTP_status"] = response.status
                self.send_response(response.status)
                self.send_header(
                    "Content-Type", response.getheader("Content-Type", "application/octet-stream")
                )
                self.send_header("Connection", "close")
                self.end_headers()

                def receive():
                    pending = b""
                    size = 0
                    first_visible = False
                    terminal_seen = False
                    try:
                        with (directory / "upstream.sse").open("xb") as raw:
                            while chunk := response.read1(65536):
                                size += len(chunk)
                                if size > MAX_BYTES:
                                    raise ValueError("bounded upstream SSE")
                                received = event("upstream_read", bytes=len(chunk))
                                raw.write(chunk)
                                raw.flush()
                                os.fsync(raw.fileno())
                                pending += chunk
                                while match := re.search(rb"\r?\n\r?\n", pending):
                                    frame, pending = pending[: match.end()], pending[match.end() :]
                                    if re.fullmatch(rb"data:\s*\[DONE\]\s*", frame.strip()):
                                        terminal_seen = True
                                    if content(frame) and not first_visible:
                                        first_visible = True
                                        outcome["first_visible_upstream_receive_ns"] = received
                                    frames.put(frame, timeout=5)
                            if pending:
                                event("upstream_unframed_tail")
                                frames.put(pending, timeout=5)
                        outcome["upstream_HTTP_EOF"] = True
                        outcome["upstream_complete"] = terminal_seen
                    except Exception as exc:
                        outcome["upstream_error"] = type(exc).__name__
                        event("upstream_error", error_class=type(exc).__name__)
                    finally:
                        frames.put(None, timeout=5)

                receiver = threading.Thread(target=receive, daemon=True)
                receiver.start()
                buffered = []
                first_visible = False
                closed = False
                with (directory / "downstream.sse").open("xb") as downstream:

                    def send(frame):
                        nonlocal closed, first_visible
                        visible = bool(content(frame))
                        if visible and not first_visible:
                            first_visible = True
                            if arm == "delay100":
                                outcome["delay_begin_ns"] = event(
                                    "delay_begin", requested_seconds=DELAY_SECONDS
                                )
                                time.sleep(DELAY_SECONDS)
                                outcome["delay_end_ns"] = event("delay_end")
                                outcome["injection_applied"] = True
                            outcome["first_visible_downstream_write_ns"] = event(
                                "first_visible_downstream_write"
                            )
                        if not closed:
                            try:
                                self.wfile.write(frame)
                                self.wfile.flush()
                                downstream.write(frame)
                                downstream.flush()
                                os.fsync(downstream.fileno())
                                event("downstream_write", bytes=len(frame))
                            except OSError:
                                closed = True
                                event("client_write_failed")
                        if arm == "earlyclose" and visible and not closed:
                            self.connection.shutdown(socket.SHUT_WR)
                            closed = True
                            outcome["injection_applied"] = True
                            event("intentional_downstream_close_before_terminal")

                    while True:
                        frame = frames.get(timeout=35)
                        if frame is None:
                            break
                        if arm == "wrongnonce":
                            buffered.append(frame)
                        else:
                            send(frame)
                    if arm == "wrongnonce":
                        messages = "\n".join(
                            str(m.get("content", "")) for m in body.get("messages", [])
                        )
                        found = re.search(r"exact code (CODE-[A-Z]{4}-[0-9]{4})", messages)
                        transformed, applied = wrong_nonce_frames(
                            buffered, found.group(1) if found else None
                        )
                        outcome["injection_applied"] = applied
                        event("nonce_diagnostic_buffered_full_upstream", applied=applied)
                        for frame in transformed:
                            send(frame)
                receiver.join(timeout=3)
                outcome["receiver_stopped"] = not receiver.is_alive()
            except Exception as exc:
                outcome["proxy_error"] = type(exc).__name__
                event("proxy_error", error_class=type(exc).__name__)
            finally:
                if response is not None:
                    response.close()
                connection.close()
                event("request_finished")
                events.close()
                for name in ("upstream.sse", "downstream.sse", "events.jsonl"):
                    path = directory / name
                    if path.exists():
                        outcome[name] = {
                            "bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        }
                (directory / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
                with active_lock:
                    active.discard(label)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.study_active = active
    server.study_active_lock = active_lock
    return server
