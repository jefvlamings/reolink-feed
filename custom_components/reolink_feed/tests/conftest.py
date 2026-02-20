"""Pytest configuration for Reolink Feed tests."""

from __future__ import annotations

import warnings


warnings.filterwarnings(
    "ignore",
    message="Inheritance class HomeAssistantApplication from web.Application is discouraged",
    category=DeprecationWarning,
    module=r"homeassistant\.components\.http\.__init__",
)
