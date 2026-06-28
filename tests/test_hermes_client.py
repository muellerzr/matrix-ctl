"""Tests for the JSON-lines Hermes IPC client."""

from __future__ import annotations

import json

import pytest

from matrixctl.client import MatrixError
from matrixctl.hermes_client import (
    HermesClient,
    HermesProtocolError,
    HermesUnavailable,
    MAX_MESSAGE_BYTES,
)
from conftest import _err, _ok


def test_request_response_roundtrip_and_correlation(make_hermes_server):
    seen = {}

    def handler(req):
        seen.update(req)
        return _ok(req, {"event_id": "$abc"})

    _, path = make_hermes_server(handler)
    client = HermesClient(path, timeout=2)
    result = client.call("send", {"room_id": "!r:s", "message": "hi"})

    assert result == {"event_id": "$abc"}
    # The request carried a version, a uuid id, the action and params.
    assert seen["version"] == 1
    assert seen["action"] == "send"
    assert seen["params"] == {"room_id": "!r:s", "message": "hi"}
    assert isinstance(seen["id"], str) and len(seen["id"]) >= 8


def test_remote_failure_maps_to_matrix_error(make_hermes_server):
    def handler(req):
        return _err(req, "M_FORBIDDEN", "nope")

    _, path = make_hermes_server(handler)
    with pytest.raises(MatrixError) as exc:
        HermesClient(path, timeout=2).call("send", {"room_id": "!r:s"})
    assert exc.value.errcode == "M_FORBIDDEN"
    assert "nope" in str(exc.value)


def test_id_mismatch_is_protocol_error(make_hermes_server):
    def handler(req):
        resp = _ok(req, {"event_id": "$x"})
        resp["id"] = "not-the-right-id"
        return resp

    _, path = make_hermes_server(handler)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("send")


def test_version_mismatch_is_protocol_error(make_hermes_server):
    def handler(req):
        resp = _ok(req, {})
        resp["version"] = 999
        return resp

    _, path = make_hermes_server(handler)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("whoami")


def test_malformed_json_is_protocol_error(make_hermes_server):
    def handler(req):
        return b"this is not json\n"

    _, path = make_hermes_server(handler)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("whoami")


def test_oversized_response_is_rejected(make_hermes_server):
    def handler(req):
        # No newline, larger than the cap: the client must bail, not OOM.
        return b"{" + b"a" * (MAX_MESSAGE_BYTES + 1024)

    _, path = make_hermes_server(handler)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("whoami")


def test_closed_without_reply_is_protocol_error(make_hermes_server):
    def handler(req):
        return None  # accept then hang up

    _, path = make_hermes_server(handler)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("whoami")


def test_missing_socket_is_unavailable(tmp_path):
    missing = str(tmp_path / "nope.sock")
    with pytest.raises(HermesUnavailable):
        HermesClient(missing, timeout=1).call("whoami")


def test_request_too_large_is_rejected(make_hermes_server):
    _, path = make_hermes_server(lambda req: _ok(req, {}))
    huge = "x" * (MAX_MESSAGE_BYTES + 10)
    with pytest.raises(HermesProtocolError):
        HermesClient(path, timeout=2).call("send", {"message": huge})
