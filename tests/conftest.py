"""Shared test fakes: an in-process Hermes IPC gateway and a fake REST client.

Neither talks to a real homeserver. The fake gateway is a threaded blocking
Unix-socket server speaking the JSON-lines protocol; the fake REST client
stands in for ``MatrixClient`` so REST-path tests don't need the network.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any, Callable

import pytest


class FakeHermesServer:
    """A minimal Hermes gateway for tests.

    ``handler(request_dict)`` returns either a response ``dict`` (JSON-encoded
    with a trailing newline), raw ``bytes`` (sent verbatim — for malformed /
    oversized cases), or ``None`` (close without replying). Every received
    request is recorded in ``.requests``.
    """

    def __init__(self, path: str, handler: Callable[[dict], Any]) -> None:
        self.path = path
        self.handler = handler
        self.requests: list[dict] = []
        self._running = True
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(path)
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    line = self._recv_line(conn)
                    if line is None:
                        continue
                    request = json.loads(line)
                    self.requests.append(request)
                    response = self.handler(request)
                    if response is None:
                        continue
                    if isinstance(response, (bytes, bytearray)):
                        conn.sendall(bytes(response))
                    else:
                        conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
                except Exception:  # noqa: BLE001 - test server, swallow & move on
                    continue

    @staticmethod
    def _recv_line(conn: socket.socket) -> bytes | None:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return buf or None
            buf += chunk
        return buf.split(b"\n", 1)[0]

    def close(self) -> None:
        self._running = False
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def echo_handler(request: dict) -> dict:
    """Default success handler: echoes version+id and answers per action."""
    action = request.get("action")
    params = request.get("params") or {}
    results: dict[str, Any] = {
        "whoami": {"user_id": "@bot:server", "device_id": "DEV", "is_guest": False},
        "send": {"event_id": "$evt-from-hermes"},
        "create_room": {"room_id": "!new:server"},
        "invite": {},
        "leave": {},
        "forget": {},
        "set_room_name": {"event_id": "$evt-name"},
        "set_room_topic": {"event_id": "$evt-topic"},
        "set_display_name": {},
        "set_room_display_name": {"event_id": "$evt-member"},
    }
    if action == "create_topic_room":
        result: dict[str, Any] = {
            "room_id": "!topic:server",
            "name": params.get("name"),
            "encrypted": bool(params.get("encrypted")),
            "invited": params.get("invite") or [],
        }
        if params.get("bot_room_display_name"):
            result["bot_room_display_name"] = params["bot_room_display_name"]
        if params.get("welcome_message"):
            # Hermes sends the welcome encrypted and returns its event id.
            result["welcome_event_id"] = "$welcome-encrypted"
        return _ok(request, result)
    return _ok(request, results.get(action, {}))


def _ok(request: dict, result: dict) -> dict:
    return {
        "version": request.get("version"),
        "id": request.get("id"),
        "ok": True,
        "result": result,
    }


def _err(request: dict, code: str, message: str) -> dict:
    return {
        "version": request.get("version"),
        "id": request.get("id"),
        "ok": False,
        "error": {"code": code, "message": message},
    }


@pytest.fixture
def hermes_server(tmp_path):
    """Start a fake gateway with the default echo handler; yield (server, path)."""
    path = str(tmp_path / "hermes.sock")
    server = FakeHermesServer(path, echo_handler)
    try:
        yield server, path
    finally:
        server.close()


@pytest.fixture
def make_hermes_server(tmp_path):
    """Factory to start a fake gateway with a custom handler."""
    servers: list[FakeHermesServer] = []

    def _make(handler: Callable[[dict], Any], name: str = "hermes.sock"):
        path = str(tmp_path / name)
        server = FakeHermesServer(path, handler)
        servers.append(server)
        return server, path

    try:
        yield _make
    finally:
        for s in servers:
            s.close()


class FakeMatrixClient:
    """Stand-in for matrixctl.client.MatrixClient (no network)."""

    def __init__(self, *, encrypted_rooms: set[str] | None = None) -> None:
        self.encrypted_rooms = encrypted_rooms or set()
        self.sent: list[tuple[str, str]] = []
        self.closed = False

    def whoami(self) -> dict:
        return {"user_id": "@bot:server", "device_id": "DEV", "is_guest": False}

    def user_id(self) -> str:
        return "@bot:server"

    def create_room(self, name, *, topic=None, invite=None, encrypted=False):
        room = "!created:server"
        if encrypted:
            self.encrypted_rooms.add(room)
        return {"room_id": room}

    def invite(self, room_id, user_id):
        return {}

    def send_message(self, room_id, body, *, msgtype="m.text"):
        self.sent.append((room_id, body))
        return {"event_id": "$evt-rest"}

    def is_room_encrypted(self, room_id) -> bool:
        return room_id in self.encrypted_rooms

    def leave(self, room_id):
        return {}

    def forget(self, room_id):
        return {}

    def set_room_name(self, room_id, name):
        return {"event_id": "$evt-name"}

    def set_room_topic(self, room_id, topic):
        return {"event_id": "$evt-topic"}

    def set_display_name(self, display_name):
        return {}

    def set_room_display_name(self, room_id, display_name):
        return {"event_id": "$evt-member"}

    def close(self):
        self.closed = True


@pytest.fixture
def fake_rest(monkeypatch):
    """Patch RestTransport.from_env to use a FakeMatrixClient; yield the client."""
    from matrixctl import transport

    client = FakeMatrixClient()

    def _from_env():
        return transport.RestTransport(client)

    monkeypatch.setattr(transport.RestTransport, "from_env", staticmethod(_from_env))
    return client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep transport/env detection deterministic across tests."""
    for var in (
        "MATRIXCTL_TRANSPORT",
        "MATRIXCTL_HERMES_SOCKET",
        "MATRIX_DEFAULT_INVITEE",
        "MATRIX_TIMEOUT_SECONDS",
        "MATRIX_CONTACTS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Pin contacts to a path that doesn't exist so a real ~/.config contacts
    # file on the dev machine can't leak into tests.
    monkeypatch.setenv("MATRIX_CONTACTS_FILE", "/nonexistent/matrixctl-contacts.json")
    # Point the default socket somewhere that definitely doesn't exist, so
    # 'auto' never accidentally finds a real gateway on the dev machine.
    monkeypatch.setattr(
        "matrixctl.transport.DEFAULT_HERMES_SOCKET", str(os.devnull) + ".nope"
    )
