"""Client for the Hermes matrixctl IPC gateway.

matrixctl has no end-to-end crypto of its own. When a Hermes Matrix adapter is
running, it exposes a Unix-domain socket that speaks a small versioned
JSON-lines protocol; matrixctl forwards operations to it so they go out through
the adapter's live mautrix/Olm client (which holds the crypto state). This
module is *only* the transport client — it never opens or touches the crypto
database; that stays exclusively with the Hermes gateway process.

Wire protocol (one JSON object per line, UTF-8, newline-terminated):

    request  -> {"version": 1, "id": "<uuid>", "action": "send",
                 "params": {"room_id": "!r:s", "message": "hi"}}
    success  <- {"version": 1, "id": "<uuid>", "ok": true,
                 "result": {"event_id": "$evt"}}
    failure  <- {"version": 1, "id": "<uuid>", "ok": false,
                 "error": {"code": "...", "message": "..."}}

The client opens a fresh connection per request (matrixctl is single-shot),
correlates the response by ``id``, enforces a size cap and timeout, and maps
failures onto the same exception types the REST client uses so the CLI's exit
codes are unchanged.
"""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any

from .client import ConfigError, DEFAULT_TIMEOUT_SECONDS, MatrixError

# Protocol version this client speaks; the gateway must echo it back.
PROTOCOL_VERSION = 1

# Hard cap on a single response, to bound memory against a hostile or buggy
# peer that never sends a newline. 1 MiB is far larger than any real response.
MAX_MESSAGE_BYTES = 1 << 20

_RECV_CHUNK = 65536


class HermesUnavailable(ConfigError):
    """The Hermes IPC socket is missing or cannot be connected to.

    Subclasses :class:`ConfigError` so the CLI treats it as a configuration
    problem (exit code 2) — same class as a missing env var.
    """


class HermesProtocolError(MatrixError):
    """The gateway spoke, but the exchange was malformed or unusable.

    Subclasses :class:`MatrixError` (exit code 1) — a runtime/transport error,
    distinct from "socket isn't there" (:class:`HermesUnavailable`).
    """


class HermesClient:
    """Synchronous client for one Hermes IPC gateway socket."""

    def __init__(
        self, socket_path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return the ``result`` object on success.

        Raises :class:`HermesUnavailable` if the socket can't be reached,
        :class:`HermesProtocolError` on a malformed/oversized/mis-correlated
        exchange, or :class:`MatrixError` (carrying the remote ``code``) when
        the gateway reports a failure.
        """
        request_id = str(uuid.uuid4())
        payload = {
            "version": PROTOCOL_VERSION,
            "id": request_id,
            "action": action,
            "params": params or {},
        }
        line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(line) > MAX_MESSAGE_BYTES:
            raise HermesProtocolError(
                f"request for action {action!r} exceeds the {MAX_MESSAGE_BYTES} "
                "byte limit"
            )

        raw = self._exchange(line, action)
        return self._parse_response(raw, request_id, action)

    # -- networking --------------------------------------------------------

    def _exchange(self, line: bytes, action: str) -> bytes:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            try:
                sock.connect(self.socket_path)
            except OSError as exc:
                raise HermesUnavailable(
                    f"could not connect to Hermes IPC socket {self.socket_path!r}: "
                    f"{exc}. Is the Hermes Matrix adapter running with its IPC "
                    "server enabled?"
                ) from exc
            try:
                sock.sendall(line)
                return self._read_line(sock, action)
            except socket.timeout as exc:
                raise HermesProtocolError(
                    f"timed out after {self.timeout}s waiting for the Hermes "
                    f"gateway to answer action {action!r}"
                ) from exc
            except OSError as exc:
                raise HermesProtocolError(
                    f"I/O error talking to the Hermes gateway: {exc}"
                ) from exc
        finally:
            sock.close()

    def _read_line(self, sock: socket.socket, action: str) -> bytes:
        """Read one newline-delimited frame, bounded by ``MAX_MESSAGE_BYTES``."""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(_RECV_CHUNK)
            if not chunk:  # peer closed
                break
            newline = chunk.find(b"\n")
            if newline != -1:
                chunk = chunk[:newline]  # ignore anything after the first frame
                total += len(chunk)
                if total > MAX_MESSAGE_BYTES:
                    raise HermesProtocolError("Hermes response exceeded size limit")
                chunks.append(chunk)
                return b"".join(chunks)
            total += len(chunk)
            if total > MAX_MESSAGE_BYTES:
                raise HermesProtocolError("Hermes response exceeded size limit")
            chunks.append(chunk)

        data = b"".join(chunks)
        if not data:
            raise HermesProtocolError(
                f"Hermes gateway closed the connection without answering "
                f"action {action!r}"
            )
        return data  # tolerate a final frame with no trailing newline

    # -- decoding ----------------------------------------------------------

    def _parse_response(
        self, raw: bytes, request_id: str, action: str
    ) -> dict[str, Any]:
        try:
            response = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HermesProtocolError(
                f"Hermes gateway returned invalid JSON for action {action!r}: {exc}"
            ) from exc

        if not isinstance(response, dict):
            raise HermesProtocolError("Hermes response was not a JSON object")

        version = response.get("version")
        if version != PROTOCOL_VERSION:
            raise HermesProtocolError(
                f"Hermes gateway speaks protocol version {version!r}, "
                f"this matrixctl speaks {PROTOCOL_VERSION}"
            )

        # Correlate: a mismatched id means we can't trust this is our reply.
        if response.get("id") != request_id:
            raise HermesProtocolError(
                "Hermes response id did not match the request id"
            )

        if response.get("ok"):
            result = response.get("result")
            return result if isinstance(result, dict) else {}

        error = response.get("error")
        if not isinstance(error, dict):
            raise HermesProtocolError(
                f"Hermes gateway reported failure for action {action!r} "
                "without a usable error object"
            )
        code = error.get("code")
        message = error.get("message") or "Hermes gateway reported an error"
        raise MatrixError(
            f"{code + ': ' if code else ''}{message}",
            errcode=code if isinstance(code, str) else None,
        )
