"""End-to-end CLI tests: JSON output, exit codes, transport routing."""

from __future__ import annotations

import json

import pytest

from matrixctl import cli
from conftest import _err, echo_handler


def run(argv, capsys):
    """Run main(argv); return (exit_code, parsed_stdout_or_None, stderr)."""
    code = cli.main(argv)
    out = capsys.readouterr()
    parsed = json.loads(out.out) if out.out.strip() else None
    return code, parsed, out.err


# -- REST transport ---------------------------------------------------------


def test_rest_whoami_output(fake_rest, capsys):
    code, data, _ = run(["--transport", "rest", "whoami"], capsys)
    assert code == 0
    assert data == {"user_id": "@bot:server", "device_id": "DEV", "is_guest": False}


def test_rest_send_plaintext_room(fake_rest, capsys):
    code, data, _ = run(["--transport", "rest", "send", "!r:s", "hi"], capsys)
    assert code == 0
    assert data == {"room_id": "!r:s", "event_id": "$evt-rest"}
    assert fake_rest.sent == [("!r:s", "hi")]


def test_rest_refuses_plaintext_into_encrypted_room(fake_rest, capsys):
    fake_rest.encrypted_rooms.add("!enc:s")
    code, data, err = run(["--transport", "rest", "send", "!enc:s", "hi"], capsys)
    assert code == 3
    assert data is None
    assert fake_rest.sent == []  # nothing was sent
    assert "encrypted" in err.lower()


def test_rest_allow_plaintext_override(fake_rest, capsys):
    fake_rest.encrypted_rooms.add("!enc:s")
    code, data, _ = run(
        ["--transport", "rest", "send", "!enc:s", "hi", "--allow-plaintext"], capsys
    )
    assert code == 0
    assert fake_rest.sent == [("!enc:s", "hi")]


def test_rest_create_topic_room_encrypted_defers_welcome(fake_rest, capsys):
    code, data, _ = run(
        [
            "--transport", "rest", "create-topic-room", "Topic",
            "--encrypted", "--welcome-message", "hello",
        ],
        capsys,
    )
    assert code == 0
    # REST can't encrypt, so the welcome is handed back, not sent.
    assert data["pending_welcome_message"] == "hello"
    assert "welcome_event_id" not in data
    assert fake_rest.sent == []


# -- Hermes transport -------------------------------------------------------


def _use_hermes(monkeypatch, path):
    monkeypatch.setenv("MATRIXCTL_TRANSPORT", "hermes")
    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", path)


def test_hermes_send_routes_through_gateway(monkeypatch, hermes_server, capsys):
    server, path = hermes_server
    _use_hermes(monkeypatch, path)

    code, data, _ = run(["send", "!enc:s", "secret"], capsys)
    assert code == 0
    assert data == {"room_id": "!enc:s", "event_id": "$evt-from-hermes"}

    # The CLI forwarded a 'send' action to the gateway — it did NOT perform a
    # raw REST m.room.message PUT itself. Encryption is the gateway's job.
    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["action"] == "send"
    assert req["params"] == {"room_id": "!enc:s", "message": "secret"}


def test_hermes_send_does_not_apply_plaintext_guard(monkeypatch, hermes_server, capsys):
    # Even into an "encrypted" room, Hermes transport sends (gateway encrypts);
    # no exit-3 refusal like REST.
    server, path = hermes_server
    _use_hermes(monkeypatch, path)
    code, _, _ = run(["send", "!enc:s", "secret"], capsys)
    assert code == 0


def test_hermes_create_topic_room_is_atomic(monkeypatch, hermes_server, capsys):
    server, path = hermes_server
    _use_hermes(monkeypatch, path)

    code, data, _ = run(
        [
            "create-topic-room", "Planning",
            "--encrypted",
            "--invite", "@zach:server",
            "--bot-room-display-name", "Hermes — Planning",
            "--welcome-message", "welcome!",
        ],
        capsys,
    )
    assert code == 0
    # A single atomic IPC call did create+invite+encrypt+welcome.
    assert len(server.requests) == 1
    req = server.requests[0]
    assert req["action"] == "create_topic_room"
    assert req["params"]["encrypted"] is True
    assert req["params"]["invite"] == ["@zach:server"]
    # Gateway sent the welcome encrypted and returned its event id.
    assert data["welcome_event_id"] == "$welcome-encrypted"
    assert "pending_welcome_message" not in data


def test_hermes_remote_error_exit_1(monkeypatch, make_hermes_server, capsys):
    server, path = make_hermes_server(lambda r: _err(r, "M_FORBIDDEN", "no"))
    _use_hermes(monkeypatch, path)
    code, data, err = run(["send", "!r:s", "hi"], capsys)
    assert code == 1
    assert data is None
    assert "no" in err


def test_hermes_required_but_unavailable_exit_2(monkeypatch, tmp_path, capsys):
    _use_hermes(monkeypatch, str(tmp_path / "absent.sock"))
    code, data, err = run(["whoami"], capsys)
    assert code == 2
    assert data is None
    assert "hermes" in err.lower()


def test_hermes_leave_forget_output(monkeypatch, hermes_server, capsys):
    server, path = hermes_server
    _use_hermes(monkeypatch, path)
    code, data, _ = run(["leave", "!r:s"], capsys)
    assert code == 0 and data == {"room_id": "!r:s", "left": True}
    code, data, _ = run(["forget", "!r:s"], capsys)
    assert code == 0 and data == {"room_id": "!r:s", "forgotten": True}


def test_hermes_set_room_display_name_includes_user_id(monkeypatch, hermes_server, capsys):
    server, path = hermes_server
    _use_hermes(monkeypatch, path)
    code, data, _ = run(["set-room-display-name", "!r:s", "Hermes"], capsys)
    assert code == 0
    assert data == {
        "room_id": "!r:s",
        "user_id": "@bot:server",
        "displayname": "Hermes",
        "event_id": "$evt-member",
    }


# -- local command (no transport) ------------------------------------------


def test_contacts_is_local(monkeypatch, capsys):
    monkeypatch.setenv("MATRIX_CONTACTS", json.dumps({"zach": "@zach:server"}))
    # Even forcing hermes, contacts must not need a gateway.
    monkeypatch.setenv("MATRIXCTL_TRANSPORT", "hermes")
    code, data, _ = run(["contacts"], capsys)
    assert code == 0
    assert data == {"contacts": {"zach": "@zach:server"}}
