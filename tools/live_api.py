from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from peer_consensus import utc_timestamp_precise

from live_state import load_state, save_state, supervisor_log_line, write_supervisor_event


def _parse_int(value: str | None, *, default: int = 0, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if maximum is not None and parsed > maximum:
        return maximum
    return parsed


class _ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], owner: "LiveControlServer") -> None:
        self.owner = owner
        super().__init__(server_address, _ControlRequestHandler)


class _ControlRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def owner(self) -> "LiveControlServer":
        return self.server.owner  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.owner.handle_get(self)

    def do_POST(self) -> None:
        self.owner.handle_post(self)


class LiveControlServer:
    def __init__(self, state: dict[str, Any], supervisor: Any) -> None:
        self.state = state
        self.supervisor = supervisor
        self.state_file = Path(state["state_file"])
        self._server: _ControlHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _control_config(self) -> dict[str, Any]:
        return self.state.setdefault("runtime", {}).setdefault("control", {})

    def _token(self) -> str:
        return str(self._control_config().get("token", "") or "")

    def _state_snapshot(self) -> dict[str, Any]:
        return load_state(self.state_file)

    def _events_path(self) -> Path:
        return Path(self.state["logs"]["events_jsonl"])

    def start(self) -> None:
        if self._server is not None:
            return
        control = self._control_config()
        host = str(control.get("host", "127.0.0.1") or "127.0.0.1")
        requested_port = _parse_int(str(control.get("port", "0") or "0"), default=0, minimum=0, maximum=65535)
        try:
            server = _ControlHTTPServer((host, requested_port), self)
        except OSError:
            if requested_port == 0:
                raise
            server = _ControlHTTPServer((host, 0), self)
        self._server = server
        actual_host, actual_port = server.server_address[:2]
        if actual_host in {"0.0.0.0", ""}:
            actual_host = host
        base_url = f"http://{actual_host}:{actual_port}"
        control["enabled"] = True
        control["host"] = actual_host
        control["port"] = actual_port
        control["base_url"] = base_url
        control["events_stream_url"] = f"{base_url}/events/stream"
        control["last_started_at"] = utc_timestamp_precise()
        save_state(self.state)
        write_supervisor_event(
            self.state,
            {
                "type": "control-server-started",
                "timestamp": utc_timestamp_precise(),
                "base_url": base_url,
                "events_stream_url": control["events_stream_url"],
            },
        )
        self._stop_event.clear()
        self._thread = threading.Thread(target=server.serve_forever, name="peer-forge-live-control", daemon=True)
        self._thread.start()
        supervisor_log_line(self.state, f"Control API listening at {base_url}")

    def shutdown(self) -> None:
        if self._server is None:
            return
        self._stop_event.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        write_supervisor_event(
            self.state,
            {
                "type": "control-server-stopped",
                "timestamp": utc_timestamp_precise(),
            },
        )
        self._server = None
        self._thread = None

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        token = self._token()
        if not token:
            return True
        header_value = handler.headers.get("X-Peer-Forge-Token", "")
        return header_value == token

    def _send_json(self, handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _send_not_found(self, handler: BaseHTTPRequestHandler) -> None:
        self._send_json(
            handler,
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "path": handler.path,
            },
        )

    def _send_unauthorized(self, handler: BaseHTTPRequestHandler) -> None:
        self._send_json(
            handler,
            HTTPStatus.UNAUTHORIZED,
            {
                "error": "unauthorized",
            },
        )

    def _read_json_body(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        content_length = _parse_int(handler.headers.get("Content-Length"), default=0, minimum=0)
        if content_length <= 0:
            return {}
        raw = handler.rfile.read(content_length)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def _events_since(self, *, after: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        events_path = self._events_path()
        if not events_path.exists():
            return [], after
        items: list[dict[str, Any]] = []
        next_after = after
        with events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for seq, line in enumerate(handle, start=1):
                if seq <= after:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    payload = {"raw": line}
                items.append({"seq": seq, "event": payload})
                next_after = seq
                if len(items) >= limit:
                    break
        return items, next_after

    def _stream_events(self, handler: BaseHTTPRequestHandler, *, after: int) -> None:
        events_path = self._events_path()
        events_path.touch(exist_ok=True)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()

        def emit(seq: int, payload: dict[str, Any]) -> None:
            rendered = json.dumps({"seq": seq, "event": payload}, ensure_ascii=True)
            handler.wfile.write(f"id: {seq}\n".encode("utf-8"))
            handler.wfile.write(b"event: supervisor\n")
            handler.wfile.write(f"data: {rendered}\n\n".encode("utf-8"))
            handler.wfile.flush()

        try:
            with events_path.open("r", encoding="utf-8", errors="replace") as stream:
                seq = 0
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    seq += 1
                    if seq <= after:
                        continue
                    payload = json.loads(line) if line.strip() else {}
                    emit(seq, payload)
                last_heartbeat = time.monotonic()
                while not self._stop_event.is_set():
                    line = stream.readline()
                    if line:
                        seq += 1
                        payload = json.loads(line) if line.strip() else {}
                        emit(seq, payload)
                        continue
                    if time.monotonic() - last_heartbeat >= 15:
                        handler.wfile.write(b": keep-alive\n\n")
                        handler.wfile.flush()
                        last_heartbeat = time.monotonic()
                    time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError, socket.error):
            return

    def handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._authorized(handler):
            self._send_unauthorized(handler)
            return
        parsed = urlparse(handler.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/health":
            snapshot = self._state_snapshot()
            self._send_json(
                handler,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "run_id": snapshot.get("run_id", ""),
                    "status": snapshot.get("status", ""),
                    "phase": snapshot.get("current_phase", ""),
                    "timestamp": utc_timestamp_precise(),
                },
            )
            return

        if path == "/state":
            self._send_json(handler, HTTPStatus.OK, self._state_snapshot())
            return

        if path == "/events":
            after = _parse_int((params.get("after") or [None])[0], default=0, minimum=0)
            limit = _parse_int((params.get("limit") or [None])[0], default=200, minimum=1, maximum=1000)
            items, next_after = self._events_since(after=after, limit=limit)
            self._send_json(
                handler,
                HTTPStatus.OK,
                {
                    "items": items,
                    "next_after": next_after,
                },
            )
            return

        if path == "/events/stream":
            after = _parse_int((params.get("after") or [None])[0], default=0, minimum=0)
            self._stream_events(handler, after=after)
            return

        self._send_not_found(handler)

    def handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._authorized(handler):
            self._send_unauthorized(handler)
            return
        parsed = urlparse(handler.path)
        if parsed.path != "/commands":
            self._send_not_found(handler)
            return
        try:
            payload = self._read_json_body(handler)
        except json.JSONDecodeError:
            self._send_json(
                handler,
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "invalid_json",
                },
            )
            return
        command = str(payload.get("command", "") or "")
        if not command.strip():
            self._send_json(
                handler,
                HTTPStatus.BAD_REQUEST,
                {
                    "error": "missing_command",
                },
            )
            return
        source = str(payload.get("source", "") or "api")
        request_id = payload.get("request_id")
        item = self.supervisor.submit_command(command, source=source, request_id=str(request_id) if request_id else None)
        self._send_json(
            handler,
            HTTPStatus.ACCEPTED,
            {
                "accepted": True,
                "request_id": item["request_id"],
                "source": item["source"],
                "queued_at": item["queued_at"],
                "command": command,
            },
        )
