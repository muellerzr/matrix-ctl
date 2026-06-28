"""Tests for transport mode resolution and selection."""

from __future__ import annotations

import argparse
import os
import socket

import pytest

from matrixctl.client import ConfigError
from matrixctl.hermes_client import HermesUnavailable
from matrixctl import transport
from matrixctl.transport import (
    HermesTransport,
    RestTransport,
    resolve_mode,
    select_transport,
)


def _args(transport_mode=None):
    return argparse.Namespace(transport=transport_mode)


def test_resolve_mode_default_is_auto():
    assert resolve_mode(_args()) == "auto"


def test_resolve_mode_flag_wins_over_env(monkeypatch):
    monkeypatch.setenv("MATRIXCTL_TRANSPORT", "rest")
    assert resolve_mode(_args("hermes")) == "hermes"


def test_resolve_mode_env_used_when_no_flag(monkeypatch):
    monkeypatch.setenv("MATRIXCTL_TRANSPORT", "hermes")
    assert resolve_mode(_args()) == "hermes"


def test_resolve_mode_invalid_raises():
    with pytest.raises(ConfigError):
        resolve_mode(_args("bogus"))


def test_rest_mode_builds_rest(fake_rest):
    t = select_transport(_args("rest"))
    assert isinstance(t, RestTransport)


def test_auto_uses_hermes_when_socket_live(monkeypatch, hermes_server):
    _, path = hermes_server
    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", path)
    t = select_transport(_args("auto"))
    assert isinstance(t, HermesTransport)


def test_auto_falls_back_to_rest_when_no_socket(monkeypatch, fake_rest, tmp_path):
    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", str(tmp_path / "absent.sock"))
    t = select_transport(_args("auto"))
    assert isinstance(t, RestTransport)


def test_hermes_mode_requires_socket(monkeypatch, tmp_path):
    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", str(tmp_path / "absent.sock"))
    with pytest.raises(HermesUnavailable):
        select_transport(_args("hermes"))


def test_hermes_mode_connects_when_live(monkeypatch, hermes_server):
    _, path = hermes_server
    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", path)
    t = select_transport(_args("hermes"))
    assert isinstance(t, HermesTransport)


def test_stale_socket_is_not_connectable(monkeypatch, tmp_path, fake_rest):
    """A bound-but-not-listening socket file exists yet refuses connections."""
    path = str(tmp_path / "stale.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    s.close()  # file remains on disk, nothing listening
    assert os.path.exists(path)

    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", path)
    # auto: stale socket -> fall back to REST.
    assert isinstance(select_transport(_args("auto")), RestTransport)
    # hermes: stale socket -> clear failure.
    with pytest.raises(HermesUnavailable):
        select_transport(_args("hermes"))


def test_symlinked_socket_is_rejected(monkeypatch, tmp_path, hermes_server, fake_rest):
    """A symlink at the socket path must not be followed."""
    _, real = hermes_server
    link = str(tmp_path / "link.sock")
    os.symlink(real, link)

    monkeypatch.setenv("MATRIXCTL_HERMES_SOCKET", link)
    # auto: symlink rejected -> REST fallback even though target is live.
    assert isinstance(select_transport(_args("auto")), RestTransport)
    with pytest.raises(HermesUnavailable):
        select_transport(_args("hermes"))


def test_invalid_timeout_env_raises(monkeypatch):
    monkeypatch.setenv("MATRIX_TIMEOUT_SECONDS", "abc")
    with pytest.raises(ConfigError):
        select_transport(_args("rest"))
