import pytest

from backend.config.instruments import instrument_spec_from_config
from backend.config.loader import load_config


def test_mnq_spec_matches_contract_facts():
    spec = instrument_spec_from_config(load_config(), "MNQ")
    assert spec.symbol == "MNQ"
    assert spec.point_value == 2.0
    assert spec.tick_size == 0.25
    assert spec.tick_value == pytest.approx(0.50)
    assert spec.min_quantity == 1 and spec.quantity_increment == 1
    assert spec.analysis_only is False


def test_es_is_analysis_only():
    assert instrument_spec_from_config(load_config(), "ES").analysis_only is True


def test_unknown_symbol_raises():
    with pytest.raises(ValueError):
        instrument_spec_from_config(load_config(), "XYZ")


def test_tick_grid():
    spec = instrument_spec_from_config(load_config(), "MNQ")
    assert spec.is_on_tick(20000.25) and spec.is_on_tick(20000.0)
    assert not spec.is_on_tick(20000.10)
    assert not spec.is_on_tick(float("nan"))


def test_round_down_quantity_respects_min_and_increment():
    spec = instrument_spec_from_config(load_config(), "MNQ").model_copy(
        update={"min_quantity": 2, "quantity_increment": 2}
    )
    assert spec.round_down_quantity(5) == 4
    assert spec.round_down_quantity(1) == 0


# --- owner decision 2: deterministic rounding of off-tick prices -------------------------
from backend.core.enums import TradeSide  # noqa: E402


def _mnq():
    return instrument_spec_from_config(load_config(), "MNQ")


def test_midpoint_18250_125_rounds_by_order_type():
    spec = _mnq()
    # entry: toward the current market price
    assert spec.round_entry(18250.125, market_price=18300.0) == 18250.25
    assert spec.round_entry(18250.125, market_price=18200.0) == 18250.00
    # stop: away from entry (long stop below entry -> down; short stop above entry -> up)
    assert spec.round_stop(18250.125, TradeSide.LONG) == 18250.00
    assert spec.round_stop(18250.125, TradeSide.SHORT) == 18250.25
    # target: toward entry (long target above entry -> down; short target below entry -> up)
    assert spec.round_target(18250.125, TradeSide.LONG) == 18250.00
    assert spec.round_target(18250.125, TradeSide.SHORT) == 18250.25


def test_rounding_is_idempotent_and_leaves_on_tick_prices_alone():
    spec = _mnq()
    for price in (18250.0, 18250.25, 18250.5, 18250.75):
        assert spec.round_entry(price, 18300.0) == price
        assert spec.round_stop(price, TradeSide.LONG) == price
        assert spec.round_target(price, TradeSide.SHORT) == price
    once = spec.round_stop(18250.3, TradeSide.LONG)
    assert spec.round_stop(once, TradeSide.LONG) == once


def test_rounding_is_deterministic_for_many_off_tick_values():
    spec = _mnq()
    for i in range(1, 200):
        price = 18000 + i * 0.0625  # includes many exact and inexact midpoints
        up, down = spec.round_up(price), spec.round_down(price)
        assert down <= price <= up and spec.is_on_tick(up) and spec.is_on_tick(down)
        assert up - down in (0.0, 0.25)
