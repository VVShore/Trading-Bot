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
