"""Stdlib fake LLMD for capture tests. Results from it are never validation evidence."""

import argparse
import asyncio
import hashlib
import json
import os
import re

WORDS = (" the", " of", " and", " to", " in", " that", " it", " is", " was", " for", " on",
         " with", " as", " at", " by", " from", " this", " have", " or", " one")
MODEL = "fake-model"


def output_tokens(body: dict) -> tuple[list[str], str]:
    content = body["messages"][0]["content"]
    match = re.fullmatch(r"Compute (\d+) \+ (\d+)\..*", content)
    if match:
        return list(str(int(match[1]) + int(match[2]))), "stop"
    seed = hashlib.sha256(content.encode()).digest()
    limit = body["max_tokens"]
    natural = limit if body.get("ignore_eos") else 48 + seed[0] % 16
    count = min(natural, limit)
    words = [WORDS[seed[index % 32] % len(WORDS)] for index in range(count)]
    return words, "length" if count == limit else "stop"


def sse(payload: dict | str) -> bytes:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    data = f"data: {text}\n\n".encode()
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


class Server:
    def __init__(self, args):
        self.args = args
        self.count = 0

    def chunk(self, delta: dict, finish: str | None = None) -> dict:
        return {"model": MODEL, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    async def complete(self, writer, body: dict) -> None:
        self.count += 1
        args = self.args
        if args.fault == "crash" and self.count == args.fault_after:
            os._exit(1)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                     b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
        await writer.drain()
        await asyncio.sleep(args.ttft)
        words, finish = output_tokens(body)
        if args.fault == "wrong" and self.count > args.fault_after:
            words = ["0"]
        writer.write(sse(self.chunk({"role": "assistant"})))
        for word in words:
            writer.write(sse(self.chunk({"content": word})))
            await writer.drain()
            await asyncio.sleep(args.gap)
        writer.write(sse(self.chunk({}, finish)))
        usage = len(words)
        if args.fault == "false_usage" and self.count > args.fault_after:
            usage *= 3
        writer.write(sse({"model": MODEL, "choices": [], "usage": {"completion_tokens": usage}}))
        writer.write(sse("[DONE]") + b"0\r\n\r\n")
        await writer.drain()

    async def handle(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            first, *lines = head.decode().split("\r\n")
            method, path, _ = first.split(" ")
            headers = {k.lower(): v.strip() for k, v in
                       (line.split(":", 1) for line in lines if ":" in line)}
            data = await reader.readexactly(int(headers.get("content-length", 0)))
            if method == "POST":
                await self.complete(writer, json.loads(data))
            else:
                text = (json.dumps({"data": [{"id": MODEL}]}) if path == "/v1/models"
                        else f"requests_total {self.count}\n")
                reply = text.encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: "
                             + str(len(reply)).encode() + b"\r\nConnection: close\r\n\r\n" + reply)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--startup", type=float, default=0.5)
    parser.add_argument("--ttft", type=float, default=0.05)
    parser.add_argument("--gap", type=float, default=0.002)
    parser.add_argument("--fault", choices=("none", "wrong", "false_usage", "crash"),
                        default="none")
    parser.add_argument("--fault-after", type=int, default=50)
    args = parser.parse_args()
    await asyncio.sleep(args.startup)
    server = await asyncio.start_server(Server(args).handle, "127.0.0.1", args.port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
