"""Command-line interface for matrixctl.

Outputs JSON on stdout, errors on stderr, exit 0 on success / non-zero on
failure. Designed to be called non-interactively by an agent.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from .client import ConfigError, EncryptedRoomError, MatrixClient, MatrixError
from .contacts import ContactError, load_contacts, resolve


def _print_json(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _err(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


# -- command handlers ------------------------------------------------------
# Each handler takes (client, args) and returns the JSON-able result to print.


def cmd_whoami(client: MatrixClient, args: argparse.Namespace) -> Any:
    return client.whoami()


def cmd_create_room(client: MatrixClient, args: argparse.Namespace) -> Any:
    result = client.create_room(
        args.name,
        topic=args.topic,
        invite=_invitees(args.invite),
        encrypted=args.encrypted,
    )
    return {"room_id": result["room_id"]}


def cmd_invite(client: MatrixClient, args: argparse.Namespace) -> Any:
    user_id = resolve(args.user_id, load_contacts())
    client.invite(args.room_id, user_id)
    return {"room_id": args.room_id, "invited": user_id}


def cmd_send(client: MatrixClient, args: argparse.Namespace) -> Any:
    if not args.allow_plaintext and client.is_room_encrypted(args.room_id):
        raise EncryptedRoomError(args.room_id)
    result = client.send_message(args.room_id, args.message)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_leave(client: MatrixClient, args: argparse.Namespace) -> Any:
    client.leave(args.room_id)
    return {"room_id": args.room_id, "left": True}


def cmd_forget(client: MatrixClient, args: argparse.Namespace) -> Any:
    client.forget(args.room_id)
    return {"room_id": args.room_id, "forgotten": True}


def cmd_set_room_name(client: MatrixClient, args: argparse.Namespace) -> Any:
    result = client.set_room_name(args.room_id, args.name)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_set_room_topic(client: MatrixClient, args: argparse.Namespace) -> Any:
    result = client.set_room_topic(args.room_id, args.topic)
    return {"room_id": args.room_id, "event_id": result.get("event_id")}


def cmd_set_display_name(client: MatrixClient, args: argparse.Namespace) -> Any:
    client.set_display_name(args.display_name)
    return {"user_id": client.user_id(), "displayname": args.display_name}


def cmd_set_room_display_name(
    client: MatrixClient, args: argparse.Namespace
) -> Any:
    result = client.set_room_display_name(args.room_id, args.display_name)
    return {
        "room_id": args.room_id,
        "user_id": client.user_id(),
        "displayname": args.display_name,
        "event_id": result.get("event_id"),
    }


def cmd_create_topic_room(
    client: MatrixClient, args: argparse.Namespace
) -> Any:
    invitees = _invitees(args.invite)
    result = client.create_room(
        args.name,
        topic=args.topic,
        invite=invitees,
        encrypted=args.encrypted,
    )
    room_id = result["room_id"]

    out: dict[str, Any] = {
        "room_id": room_id,
        "name": args.name,
        "encrypted": bool(args.encrypted),
        "invited": invitees,
    }

    if args.bot_room_display_name:
        client.set_room_display_name(room_id, args.bot_room_display_name)
        out["bot_room_display_name"] = args.bot_room_display_name

    if args.welcome_message:
        # matrixctl has no crypto, so it must not write plaintext into an
        # encrypted room. Hand the message back for Hermes/mautrix to send
        # encrypted; only send it directly when the room is unencrypted.
        if args.encrypted:
            out["pending_welcome_message"] = args.welcome_message
        else:
            sent = client.send_message(room_id, args.welcome_message)
            out["welcome_event_id"] = sent.get("event_id")

    return out


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


def cmd_contacts(client: MatrixClient, args: argparse.Namespace) -> Any:
    return {"contacts": load_contacts()}


# -- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrixctl",
        description="Non-interactive CLI for automating Matrix.",
    )
    parser.add_argument("--version", action="version", version=__version__)
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
        help="Send even if the room is encrypted (message goes UNENCRYPTED).",
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
            with MatrixClient.from_env() as client:
                result = args.func(client, args)
        else:
            # Purely local command (e.g. contacts) — no homeserver needed.
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
