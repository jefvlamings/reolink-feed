"""Unit tests for config flow helper coercion."""

from custom_components.reolink_feed.config_flow import _coerce_float, _coerce_int


def test_coerce_int_clamps_and_defaults() -> None:
    assert _coerce_int("12", default=24, minimum=1, maximum=100) == 12
    assert _coerce_int("bad", default=24, minimum=1, maximum=100) == 24
    assert _coerce_int(0, default=24, minimum=1, maximum=100) == 1
    assert _coerce_int(500, default=24, minimum=1, maximum=100) == 100


def test_coerce_float_clamps_and_defaults() -> None:
    assert _coerce_float("2.5", default=5.0, minimum=0.1, maximum=10.0) == 2.5
    assert _coerce_float("bad", default=5.0, minimum=0.1, maximum=10.0) == 5.0
    assert _coerce_float(0, default=5.0, minimum=0.1, maximum=10.0) == 0.1
    assert _coerce_float(99, default=5.0, minimum=0.1, maximum=10.0) == 10.0

