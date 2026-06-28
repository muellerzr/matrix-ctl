"""Command-line interface for matrixctl.

Outputs JSON on stdout, errors on stderr, exit 0 on success / non-zero on
failure. Designed to be called non-interactively by an agent.

Every command runs over a *transport* (see :mod:`matrixctl.transport`): plain
REST, or the Hermes IPC gateway for end-to-end encrypted operation. The output
shape and exit codes are identical either way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from .client import ConfigError, EncryptedRoomError, MatrixError
from .contacts import ContactError, load_contacts, resolve
from .transport import select_transport


def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _err(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


# -- command handlers ------------------------------------------------------
# Each handler takes (transport, args) and returns the JSON-able result to
# print. Handlers are transport-agnostic: the transport hides REST-vs-Hermes.


def cmd_whoami(transport: Any, args: argparse.Namespace) -> Any:
    return transport.whoami()


def cmd_create_room(transport: Any, args: argparse.Namespace) -> Any:
    result = transport.create_room(
        name=args.name,
        topic=args.topic,
        invite=_invitees(args.invite),
        encrypted=bool(args.encrypted),
    )
    return {"room_id": result["room_id"]}


def cmd_invite(transport: Any, args: argparse.Namespace) -> Any:
    user_id = resolve(args.user_id, load_contacts())
    transport.invite(args.room_id, user_id)
    return {"room_id": args.room_id, "invited": user_id}


def cmd_send(transport: Any, args: argparse.Namespace) -> Any:
    # Never silently send plaintext into an encrypted room. A transport that
    # encrypts (Hermes) handles this upstream; a plaintext one (REST) must
    # refuse unless the caller explicitly opts into an unencrypted send.
    if (
        not transport.encrypts
        and not args.allow_plaintext
        and transport.is_room_encrypted(args.room_id)
    ):
        raise EncryptedRoomError(args.room_id)
    result = transport.send(args.room_id, args.message)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_leave(transport: Any, args: argparse.Namespace) -> Any:
    transport.leave(args.room_id)
    return {"room_id": args.room_id, "left": True}


def cmd_forget(transport: Any, args: argparse.Namespace) -> Any:
    transport.forget(args.room_id)
    return {"room_id": args.room_id, "forgotten": True}


def cmd_set_room_name(transport: Any, args: argparse.Namespace) -> Any:
    result = transport.set_room_name(args.room_id, args.name)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_set_room_topic(transport: Any, args: argparse.Namespace) -> Any:
    result = transport.set_room_topic(args.room_id, args.topic)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_set_display_name(transport: Any, args: argparse.Namespace) -> Any:
    transport.set_display_name(args.display_name)
    return {"user_id": transport.user_id(), "displayname": args.display_name}


def cmd_set_room_display_name(transport: Any, args: argparse.Namespace) -> Any:
    result = transport.set_room_display_name(args.room_id, args.display_name)
    return {
        "room_id": args.room_id,
        "user_id": transport.user_id(),
        "displayname": args.display_name,
        "event_id": result.get("event_id"),
    }


def cmd_create_topic_room(transport: Any, args: argparse.Namespace) -> Any:
    return transport.create_topic_room(
        name=args.name,
        topic=args.topic,
        invite=_invitees(args.invite),
        encrypted=bool(args.encrypted),
        bot_room_display_name=args.bot_room_display_name,
        welcome_message=args.welcome_message,
    )


def _invitees(explicit: list[str] | None) -> list[str]:
    """Combine explicit --invite values with MATRIX_DEFAULT_INVITEE.

    Names are resolved against contacts (a bare name like ``zach`` becomes its
    full MXID). The default invitee is only added when no invitees were passed
    explicitly, so callers can opt out of the default by passing their own list.
    """
    raw = explicit
    if not raw:
        default = os.environ.get("MATRIX_DEFAULT_INVITEE")
        raw = [default] if default else []
    if not raw:
        return []
    contacts = load_contacts()
    return [resolve(value, contacts) for value in raw]


def cmd_contacts(transport: Any, args: argparse.Namespace) -> Any:
    return {"contacts": load_contacts()}


# -- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrixctl",
        description="Non-interactive CLI for automating Matrix.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--transport",
        choices=["auto", "rest", "hermes"],
        default=None,
        help=(
            "How to reach Matrix: 'rest' (direct, no E2E), 'hermes' (require the "
            "Hermes IPC gateway for E2E), or 'auto' (Hermes if its socket is "
            "live, else REST). Defaults to $MATRIXCTL_TRANSPORT or 'auto'."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("whoami", help="Show the authenticated user id.")
    p.set_defaults(func=cmd_whoami)

    p = sub.add_parser(
        "contacts", help="List configured name -> MXID contacts."
    )
    p.set_defaults(func=cmd_contacts, needs_client=False)

    p = sub.add_parser("create-room", help="Create a room.")
    p.add_argument("name", help="Room name.")
    p.add_argument("--encrypted", action="store_true", help="Enable E2E encryption.")
    p.add_argument("--topic", help="Room topic.")
    p.add_argument(
        "--invite",
        action="append",
        metavar="@user:server",
        help="User to invite (repeatable).",
    )
    p.set_defaults(func=cmd_create_room)

    p = sub.add_parser("invite", help="Invite a user to a room.")
    p.add_argument("room_id")
    p.add_argument("user_id")
    p.set_defaults(func=cmd_invite)

    p = sub.add_parser("send", help="Send a text message to a room.")
    p.add_argument("room_id")
    p.add_argument("message")
    p.add_argument(
        "--allow-plaintext",
        action="store_true",
        help=(
            "On the REST transport, send even if the room is encrypted (message "
            "goes UNENCRYPTED). Ignored on the Hermes transport, which always "
            "encrypts."
        ),
    )
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("leave", help="Leave a room (close chat).")
    p.add_argument("room_id")
    p.set_defaults(func=cmd_leave)

    p = sub.add_parser("forget", help="Forget a room.")
    p.add_argument("room_id")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("set-room-name", help="Set a room's name.")
    p.add_argument("room_id")
    p.add_argument("name")
    p.set_defaults(func=cmd_set_room_name)

    p = sub.add_parser("set-room-topic", help="Set a room's topic.")
    p.add_argument("room_id")
    p.add_argument("topic")
    p.set_defaults(func=cmd_set_room_topic)

    p = sub.add_parser("set-display-name", help="Set your global display name.")
    p.add_argument("display_name")
    p.set_defaults(func=cmd_set_display_name)

    p = sub.add_parser(
        "set-room-display-name", help="Set your per-room display name."
    )
    p.add_argument("room_id")
    p.add_argument("display_name")
    p.set_defaults(func=cmd_set_room_display_name)

    p = sub.add_parser(
        "create-topic-room",
        help="Create a private topic room and set it up in one call.",
    )
    p.add_argument("name", help="Topic / room name.")
    p.add_argument("--topic", help="Room topic description.")
    p.add_argument(
        "--invite",
        action="append",
        metavar="@user:server",
        help="User to invite (repeatable).",
    )
    p.add_argument("--encrypted", action="store_true", help="Enable E2E encryption.")
    p.add_argument(
        "--bot-room-display-name",
        help="Set the bot's per-room display name in the new room.",
    )
    p.add_argument(
        "--welcome-message",
        help="Send this message into the room after creation.",
    )
    p.set_defaults(func=cmd_create_topic_room)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if getattr(args, "needs_client", True):
            with select_transport(args) as transport:
                result = args.func(transport, args)
        else:
            # Purely local command (e.g. contacts) — no transport needed.
            result = args.func(None, args)
    except (ConfigError, ContactError) as exc:
        _err(str(exc))
        return 2
    except EncryptedRoomError as exc:
        _err(str(exc))
        return 3
    except MatrixError as exc:
        _err(str(exc))
        return 1
    except KeyboardInterrupt:  # pragma: no cover
        _err("interrupted")
        return 130

    _print_json(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
