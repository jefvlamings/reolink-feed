"""Unit tests for integration init helper functions."""

from __future__ import annotations

import asyncio

import pytest

import custom_components.reolink_feed as integration
from custom_components.reolink_feed.const import CARD_URL_PATH


class _FakeHass:
    """Minimal hass stub for helper unit tests."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.executor_calls = 0

    async def async_add_executor_job(self, func, *args):
        self.executor_calls += 1
        return func(*args)


def test_async_integration_version_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    hass = _FakeHass()
    monkeypatch.setattr(integration, "_integration_version", lambda: "9.9.9")

    version_a = asyncio.run(integration._async_integration_version(hass))
    version_b = asyncio.run(integration._async_integration_version(hass))

    assert version_a == "9.9.9"
    assert version_b == "9.9.9"
    assert hass.executor_calls == 1
    assert hass.data[integration._INTEGRATION_VERSION_CACHE_KEY] == "9.9.9"


def test_card_resource_url_uses_version_argument() -> None:
    assert integration._card_resource_url("1.2.3") == f"{CARD_URL_PATH}?v=1.2.3"


def test_resource_version_from_url_parses_query() -> None:
    assert integration._resource_version_from_url("/reolink_feed/reolink-feed-card.js?v=2.0.0") == "2.0.0"
    assert integration._resource_version_from_url("/reolink_feed/reolink-feed-card.js") is None
