"""Conditional-fetch behavior. No live network anywhere — httpx.MockTransport only."""

import httpx
import pytest

from intern_queue.sources import base
from intern_queue.sources.base import conditional_get


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_etag_stored_then_sent_and_304_means_zero_db_writes(con):
    calls = []

    def handler(request):
        calls.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"'}, json=[])

    client = make_client(handler)
    first = conditional_get(client, con, "https://example.test/listings.json")
    assert first is not None and calls[0] is None

    changes_before = con.total_changes
    second = conditional_get(client, con, "https://example.test/listings.json")
    assert second is None  # 304
    assert calls[1] == '"v1"'  # the stored etag was sent
    assert con.total_changes == changes_before  # a 304 performs zero DB writes


def test_user_agent_identifies_the_tool(con):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json=[])

    conditional_get(make_client(handler), con, "https://example.test/x")
    assert "intern-queue" in seen["ua"]
    assert "personal" in seen["ua"]


def test_backoff_retries_on_5xx_then_succeeds(con, monkeypatch):
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", sleeps.append)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[])

    resp = conditional_get(make_client(handler), con, "https://example.test/flaky")
    assert resp is not None and len(attempts) == 3
    assert sleeps == [2.0, 4.0]  # exponential backoff


def test_gives_up_after_retries(con, monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)

    def handler(request):
        return httpx.Response(429)

    with pytest.raises(httpx.HTTPStatusError):
        conditional_get(make_client(handler), con, "https://example.test/limited")


def test_4xx_raises_without_retry(con, monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: pytest.fail("must not sleep on 4xx"))
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        conditional_get(make_client(handler), con, "https://example.test/gone")
    assert len(attempts) == 1
