"""Friendly-name -> Matrix ID (MXID) resolution.

Lets callers (and agents) write ``--invite zach`` instead of the full
``@zach:server`` MXID. Contacts come from two sources, merged with the inline
env var taking precedence over the file:

1. A JSON file at ``~/.config/matrixctl/contacts.json`` (override the path
   with ``MATRIX_CONTACTS_FILE``), shaped ``{"zach": "@zach:server"}``.
2. An inline ``MATRIX_CONTACTS`` env var holding the same JSON object.

A value that already looks like an MXID (``@localpart:server``) is passed
through untouched, so callers can always use full MXIDs regardless of contacts.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Matrix user id, e.g. @hermes:example.org — localpart, then ':', then domain.
_MXID_RE = re.compile(r"^@[^:\s]+:[^\s]+$")

DEFAULT_CONTACTS_PATH = Path.home() / ".config" / "matrixctl" / "contacts.json"


class ContactError(Exception):
    """Raised when a name can't be resolved to an MXID."""


def is_mxid(value: str) -> bool:
    return bool(_MXID_RE.match(value))


def _normalize(raw: object, source: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ContactError(f"{source} must be a JSON object of name -> MXID")
    out: dict[str, str] = {}
    for key, mxid in raw.items():
        if not isinstance(mxid, str) or not is_mxid(mxid):
            raise ContactError(
                f"{source}: contact {key!r} -> {mxid!r} is not a valid MXID"
            )
        # Match by bare name so both "zach" and "@zach" look the same.
        out[str(key).lstrip("@")] = mxid
    return out


def load_contacts() -> dict[str, str]:
    """Load and merge contacts from file then inline env (env wins)."""
    contacts: dict[str, str] = {}

    path = Path(os.environ.get("MATRIX_CONTACTS_FILE") or DEFAULT_CONTACTS_PATH)
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ContactError(f"could not read contacts file {path}: {exc}") from exc
        contacts.update(_normalize(raw, str(path)))

    inline = os.environ.get("MATRIX_CONTACTS")
    if inline:
        try:
            raw = json.loads(inline)
        except ValueError as exc:
            raise ContactError(f"MATRIX_CONTACTS is not valid JSON: {exc}") from exc
        contacts.update(_normalize(raw, "MATRIX_CONTACTS"))

    return contacts


def resolve(value: str, contacts: dict[str, str]) -> str:
    """Resolve a name or MXID to an MXID, or raise ContactError."""
    value = value.strip()
    if is_mxid(value):
        return value
    key = value.lstrip("@")
    if key in contacts:
        return contacts[key]
    known = ", ".join(sorted(contacts)) or "(none configured)"
    raise ContactError(
        f"unknown contact {value!r}: not an MXID (@user:server) and not in "
        f"contacts. Known contacts: {known}"
    )
