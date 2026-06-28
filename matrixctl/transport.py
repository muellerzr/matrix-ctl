"""Transport selection for matrixctl.

A *transport* is how matrixctl actually talks to Matrix. Two exist:

- :class:`RestTransport` — the original behaviour: direct Client-Server REST
  calls with access-token auth. Has no end-to-end crypto, so it refuses to send
  plaintext into an encrypted room.
- :class:`HermesTransport` — forwards each operation to a running Hermes Matrix
  adapter over its IPC socket, so messages go out through the adapter's live
  mautrix/Olm client and are end-to-end encrypted. matrixctl never opens the
  crypto database itself.

Both expose the same small interface, so the CLI handlers are transport-agnostic
and the printed JSON / exit codes are identical regardless of which is used.
"""

from __future__ import annotations

import os
import socket
import stat
from typing import Any

from .client import ConfigError, DEFAULT_TIMEOUT_SECONDS, MatrixClient
from .hermes_client import HermesClient, HermesUnavailable

# Default gateway socket; overridable with MATRIXCTL_HERMES_SOCKET.
DEFAULT_HERMES_SOCKET = "/run/matrixctl/hermes.sock"

VALID_MODES = ("auto", "rest", "hermes")


def _env_timeout() -> float:
    """Read MATRIX_TIMEOUT_SECONDS the same way the REST client does."""
    raw = os.environ.get("MATRIX_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(
            f"MATRIX_TIMEOUT_SECONDS must be a number, got {raw!r}"
        ) from exc


# -- REST -------------------------------------------------------------------


class RestTransport:
    """Direct Client-Server REST transport (no end-to-end crypto)."""

    name = "rest"
    encrypts = False  # cannot encrypt message bodies

    def __init__(self, client: MatrixClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> "RestTransport":
        return cls(MatrixClient.from_env())

    def __enter__(self) -> "RestTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    # operations
    def whoami(self) -> dict[str, Any]:
        return self._client.whoami()

    def user_id(self) -> str:
        return self._client.user_id()

    def create_room(
        self,
        *,
        name: str,
        topic: str | None,
        invite: list[str],
        encrypted: bool,
    ) -> dict[str, Any]:
        return self._client.create_room(
            name, topic=topic, invite=invite, encrypted=encrypted
        )

    def invite(self, room_id: str, user_id: str) -> dict[str, Any]:
        return self._client.invite(room_id, user_id)

    def send(self, room_id: str, message: str) -> dict[str, Any]:
        return self._client.send_message(room_id, message)

    def is_room_encrypted(self, room_id: str) -> bool:
        return self._client.is_room_encrypted(room_id)

    def leave(self, room_id: str) -> dict[str, Any]:
        return self._client.leave(room_id)

    def forget(self, room_id: str) -> dict[str, Any]:
        return self._client.forget(room_id)

    def set_room_name(self, room_id: str, name: str) -> dict[str, Any]:
        return self._client.set_room_name(room_id, name)

    def set_room_topic(self, room_id: str, topic: str) -> dict[str, Any]:
        return self._client.set_room_topic(room_id, topic)

    def set_display_name(self, display_name: str) -> dict[str, Any]:
        return self._client.set_display_name(display_name)

    def set_room_display_name(
        self, room_id: str, display_name: str
    ) -> dict[str, Any]:
        return self._client.set_room_display_name(room_id, display_name)

    def create_topic_room(
        self,
        *,
        name: str,
        topic: str | None,
        invite: list[str],
        encrypted: bool,
        bot_room_display_name: str | None,
        welcome_message: str | None,
    ) -> dict[str, Any]:
        """Create + set up a private room with multiple REST calls.

        Because REST cannot encrypt, the welcome message for an *encrypted*
        room is returned as ``pending_welcome_message`` rather than sent.
        """
        result = self._client.create_room(
            name, topic=topic, invite=invite, encrypted=encrypted
        )
        room_id = result["room_id"]
        out: dict[str, Any] = {
            "room_id": room_id,
            "name": name,
            "encrypted": bool(encrypted),
            "invited": invite,
        }
        if bot_room_display_name:
            self._client.set_room_display_name(room_id, bot_room_display_name)
            out["bot_room_display_name"] = bot_room_display_name
        if welcome_message:
            if encrypted:
                out["pending_welcome_message"] = welcome_message
            else:
                sent = self._client.send_message(room_id, welcome_message)
                out["welcome_event_id"] = sent.get("event_id")
        return out


# -- Hermes IPC -------------------------------------------------------------


class HermesTransport:
    """Forwards operations to the Hermes adapter over its IPC socket.

    Sends go through the adapter's live mautrix client, so message bodies are
    end-to-end encrypted by Hermes — matrixctl never handles crypto. Because
    encryption is handled upstream, ``encrypts`` is True and the CLI does not
    apply the plaintext-into-encrypted-room guard for this transport.
    """

    name = "hermes"
    encrypts = True

    def __init__(
        self, socket_path: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        self._client = HermesClient(socket_path, timeout=timeout)
        self._user_id: str | None = None

    def __enter__(self) -> "HermesTransport":
        return self

    def __exit__(self, *exc: object) -> None:
        # Connections are per-call; nothing persistent to close.
        return None

    # operations
    def whoami(self) -> dict[str, Any]:
        return self._client.call("whoami")

    def user_id(self) -> str:
        if not self._user_id:
            self._user_id = str(self.whoami().get("user_id") or "")
        return self._user_id

    def create_room(
        self,
        *,
        name: str,
        topic: str | None,
        invite: list[str],
        encrypted: bool,
    ) -> dict[str, Any]:
        return self._client.call(
            "create_room",
            {"name": name, "topic": topic, "invite": invite, "encrypted": encrypted},
        )

    def invite(self, room_id: str, user_id: str) -> dict[str, Any]:
        return self._client.call("invite", {"room_id": room_id, "user_id": user_id})

    def send(self, room_id: str, message: str) -> dict[str, Any]:
        return self._client.call("send", {"room_id": room_id, "message": message})

    def is_room_encrypted(self, room_id: str) -> bool:
        # Unused: encrypts=True means the CLI never calls this for the guard.
        # Hermes encrypts whenever the room requires it.
        return True

    def leave(self, room_id: str) -> dict[str, Any]:
        return self._client.call("leave", {"room_id": room_id})

    def forget(self, room_id: str) -> dict[str, Any]:
        return self._client.call("forget", {"room_id": room_id})

    def set_room_name(self, room_id: str, name: str) -> dict[str, Any]:
        return self._client.call(
            "set_room_name", {"room_id": room_id, "name": name}
        )

    def set_room_topic(self, room_id: str, topic: str) -> dict[str, Any]:
        return self._client.call(
            "set_room_topic", {"room_id": room_id, "topic": topic}
        )

    def set_display_name(self, display_name: str) -> dict[str, Any]:
        return self._client.call(
            "set_display_name", {"display_name": display_name}
        )

    def set_room_display_name(
        self, room_id: str, display_name: str
    ) -> dict[str, Any]:
        return self._client.call(
            "set_room_display_name",
            {"room_id": room_id, "display_name": display_name},
        )

    def create_topic_room(
        self,
        *,
        name: str,
        topic: str | None,
        invite: list[str],
        encrypted: bool,
        bot_room_display_name: str | None,
        welcome_message: str | None,
    ) -> dict[str, Any]:
        """One atomic IPC call; Hermes does create+invite+encrypt+welcome."""
        return self._client.call(
            "create_topic_room",
            {
                "name": name,
                "topic": topic,
                "invite": invite,
                "encrypted": encrypted,
                "bot_room_display_name": bot_room_display_name,
                "welcome_message": welcome_message,
            },
        )


# -- selection --------------------------------------------------------------


def hermes_socket_path() -> str:
    return os.environ.get("MATRIXCTL_HERMES_SOCKET") or DEFAULT_HERMES_SOCKET


def _is_socket(path: str) -> bool:
    """True only if ``path`` is a real socket and not a symlink.

    We ``lstat`` (not ``stat``) so a symlink at the socket path is rejected
    rather than silently followed — matrixctl should only ever connect to the
    gateway's own socket, never a planted link.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISSOCK(info.st_mode)


def _is_connectable(path: str, timeout: float) -> bool:
    """True if ``path`` is a socket we can actually connect to (not stale)."""
    if not _is_socket(path):
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(min(timeout, 5.0))
    try:
        sock.connect(path)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def resolve_mode(args: Any) -> str:
    """Resolve the transport mode from the flag, then env, defaulting to auto."""
    mode = getattr(args, "transport", None) or os.environ.get("MATRIXCTL_TRANSPORT")
    mode = (mode or "auto").lower()
    if mode not in VALID_MODES:
        raise ConfigError(
            f"invalid transport {mode!r}; expected one of {', '.join(VALID_MODES)}"
        )
    return mode


def select_transport(args: Any) -> RestTransport | HermesTransport:
    """Pick and build the transport per the resolved mode.

    - ``rest``   — always REST (original behaviour).
    - ``hermes`` — require the IPC gateway; fail clearly if unreachable.
    - ``auto``   — use Hermes when its socket is live, else fall back to REST.
    """
    mode = resolve_mode(args)
    timeout = _env_timeout()

    if mode == "rest":
        return RestTransport.from_env()

    socket_path = hermes_socket_path()

    if mode == "hermes":
        if not _is_connectable(socket_path, timeout):
            raise HermesUnavailable(
                f"transport 'hermes' was requested but the IPC socket "
                f"{socket_path!r} is not available (set MATRIXCTL_HERMES_SOCKET, "
                "or start the Hermes adapter with its IPC server enabled)"
            )
        return HermesTransport(socket_path, timeout=timeout)

    # auto: prefer Hermes when it's actually reachable, otherwise REST.
    if _is_connectable(socket_path, timeout):
        return HermesTransport(socket_path, timeout=timeout)
    return RestTransport.from_env()
