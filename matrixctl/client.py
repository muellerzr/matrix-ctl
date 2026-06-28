"""Thin wrapper around the Matrix Client-Server API.

Only access-token authentication is supported. Every method returns parsed
JSON (a dict) or raises :class:`MatrixError` on failure.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import httpx

# Standard Megolm algorithm used for end-to-end encrypted rooms.
ENCRYPTION_ALGORITHM = "m.megolm.v1.aes-sha2"

DEFAULT_TIMEOUT_SECONDS = 30.0


class MatrixError(Exception):
    """Raised when the homeserver returns an error or the request fails.

    Carries the Matrix ``errcode`` when one is available so callers can react
    to specific conditions if they ever need to.
    """

    def __init__(self, message: str, *, errcode: str | None = None,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode
        self.status_code = status_code


class ConfigError(Exception):
    """Raised when required configuration (env vars) is missing."""


def _quote(value: str) -> str:
    """Percent-encode a path segment (room ids, user ids, event types)."""
    return quote(value, safe="")


def _normalize_homeserver(homeserver: str) -> str:
    """Accept a bare hostname and default it to https://.

    Matrix homeservers are served over TLS, so a scheme-less value like
    ``matrix.example.org`` is treated as ``https://matrix.example.org``.
    """
    value = homeserver.strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    return value


class MatrixClient:
    """Minimal Matrix Client-Server API client."""

    def __init__(
        self,
        homeserver: str,
        access_token: str,
        *,
        user_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.homeserver = _normalize_homeserver(homeserver)
        self._access_token = access_token
        self._user_id = user_id
        self._client = httpx.Client(
            base_url=self.homeserver,
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        # Monotonic counter for message send transaction ids within a process.
        self._txn = 0

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "MatrixClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def from_env(cls) -> "MatrixClient":
        """Build a client from the documented environment variables."""
        homeserver = os.environ.get("MATRIX_HOMESERVER")
        token = os.environ.get("MATRIX_ACCESS_TOKEN")
        missing = [
            name
            for name, value in (
                ("MATRIX_HOMESERVER", homeserver),
                ("MATRIX_ACCESS_TOKEN", token),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Missing required environment variable(s): " + ", ".join(missing)
            )

        timeout_raw = os.environ.get("MATRIX_TIMEOUT_SECONDS")
        timeout = DEFAULT_TIMEOUT_SECONDS
        if timeout_raw:
            try:
                timeout = float(timeout_raw)
            except ValueError as exc:
                raise ConfigError(
                    f"MATRIX_TIMEOUT_SECONDS must be a number, got {timeout_raw!r}"
                ) from exc

        assert homeserver and token  # narrowed by the missing-check above
        return cls(
            homeserver,
            token,
            user_id=os.environ.get("MATRIX_USER_ID") or None,
            timeout=timeout,
        )

    # -- low-level request -------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise MatrixError(f"HTTP request failed: {exc}") from exc

        # All Matrix responses are JSON; tolerate an empty body just in case.
        try:
            data = response.json() if response.content else {}
        except ValueError:
            data = {}

        if response.is_error:
            errcode = data.get("errcode") if isinstance(data, dict) else None
            error = data.get("error") if isinstance(data, dict) else None
            message = error or f"Request failed with status {response.status_code}"
            raise MatrixError(
                f"{errcode + ': ' if errcode else ''}{message}",
                errcode=errcode,
                status_code=response.status_code,
            )

        return data if isinstance(data, dict) else {"data": data}

    # -- identity ----------------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self._request("GET", "/_matrix/client/v3/account/whoami")

    def user_id(self) -> str:
        """Return the caller's user id, resolving via whoami if necessary."""
        if not self._user_id:
            self._user_id = self.whoami()["user_id"]
        return self._user_id

    # -- rooms -------------------------------------------------------------

    def create_room(
        self,
        name: str | None = None,
        *,
        topic: str | None = None,
        invite: list[str] | None = None,
        encrypted: bool = False,
        preset: str = "private_chat",
        visibility: str = "private",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"preset": preset, "visibility": visibility}
        if name:
            body["name"] = name
        if topic:
            body["topic"] = topic
        if invite:
            body["invite"] = invite
        if encrypted:
            body["initial_state"] = [
                {
                    "type": "m.room.encryption",
                    "state_key": "",
                    "content": {"algorithm": ENCRYPTION_ALGORITHM},
                }
            ]
        return self._request("POST", "/_matrix/client/v3/createRoom", json=body)

    def invite(self, room_id: str, user_id: str) -> dict[str, Any]:
        path = f"/_matrix/client/v3/rooms/{_quote(room_id)}/invite"
        return self._request("POST", path, json={"user_id": user_id})

    def send_message(
        self, room_id: str, body: str, *, msgtype: str = "m.text"
    ) -> dict[str, Any]:
        self._txn += 1
        # Unique per send: nanosecond clock + pid + in-process counter, so two
        # rapid single-shot invocations can't collide and get deduped.
        txn_id = f"matrixctl-{time.time_ns()}-{os.getpid()}-{self._txn}"
        path = (
            f"/_matrix/client/v3/rooms/{_quote(room_id)}"
            f"/send/m.room.message/{_quote(txn_id)}"
        )
        content = {"msgtype": msgtype, "body": body}
        return self._request("PUT", path, json=content)

    def leave(self, room_id: str) -> dict[str, Any]:
        path = f"/_matrix/client/v3/rooms/{_quote(room_id)}/leave"
        return self._request("POST", path, json={})

    def forget(self, room_id: str) -> dict[str, Any]:
        path = f"/_matrix/client/v3/rooms/{_quote(room_id)}/forget"
        return self._request("POST", path, json={})

    # -- state -------------------------------------------------------------

    def _send_state(
        self, room_id: str, event_type: str, content: dict[str, Any],
        state_key: str = "",
    ) -> dict[str, Any]:
        path = (
            f"/_matrix/client/v3/rooms/{_quote(room_id)}"
            f"/state/{_quote(event_type)}/{_quote(state_key)}"
        )
        return self._request("PUT", path, json=content)

    def get_state_event(
        self, room_id: str, event_type: str, state_key: str = ""
    ) -> dict[str, Any]:
        path = (
            f"/_matrix/client/v3/rooms/{_quote(room_id)}"
            f"/state/{_quote(event_type)}/{_quote(state_key)}"
        )
        return self._request("GET", path)

    def set_room_name(self, room_id: str, name: str) -> dict[str, Any]:
        return self._send_state(room_id, "m.room.name", {"name": name})

    def set_room_topic(self, room_id: str, topic: str) -> dict[str, Any]:
        return self._send_state(room_id, "m.room.topic", {"topic": topic})

    def set_display_name(self, display_name: str) -> dict[str, Any]:
        user = self.user_id()
        path = f"/_matrix/client/v3/profile/{_quote(user)}/displayname"
        return self._request("PUT", path, json={"displayname": display_name})

    def set_room_display_name(
        self, room_id: str, display_name: str
    ) -> dict[str, Any]:
        """Set the caller's per-room display name.

        Per-room profile info lives on the user's ``m.room.member`` state
        event, so we fetch the existing membership content and merge in the
        new display name to avoid clobbering ``avatar_url`` etc.
        """
        user = self.user_id()
        try:
            content = self.get_state_event(room_id, "m.room.member", user)
        except MatrixError:
            content = {}
        content = dict(content)
        content["membership"] = "join"
        content["displayname"] = display_name
        return self._send_state(room_id, "m.room.member", content, state_key=user)
