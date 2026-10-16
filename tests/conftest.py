"""Shared fixtures. Tests in this tree must not open network connections."""

import socket

import pytest
import requests
from loguru import logger

logger.remove()


class _NoNetworkSocket(socket.socket):
    """Allows local socketpairs (used by asyncio) but refuses connects."""

    def connect(self, *_args, **_kwargs):
        raise RuntimeError("network is disabled in unit tests")

    def connect_ex(self, *_args, **_kwargs):
        raise RuntimeError("network is disabled in unit tests")


def _blocked(*_args, **_kwargs):
    raise RuntimeError("network is disabled in unit tests")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", _blocked)


class FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def fake_clock():
    return FakeClock(0)
