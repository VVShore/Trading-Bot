from datetime import datetime, timezone

import pytest

from backend.config.loader import load_config
from backend.core.enums import TradeSide
from backend.core.models.order_intent import OrderIntent
from backend.core.models.target import Target
from backend.risk.engine import RiskEngine
from tests.helpers import DECISION_TIME, make_daily_state, make_setup


def _engine(**risk_overrides):
    base = load_config()
    config = base.model_copy(update={"risk": base.risk.model_copy(update=risk_overrides)})
    return RiskEngine(config, make_daily_state(config))


def test_valid_setup_is_approved_and_sized_within_risk():
    decision = _engine().evaluate(make_setup())
    assert decision.approved is True
    intent = decision.intent
    assert isinstance(intent, OrderIntent) and intent.is_authentic
    # risk/contract = 10pt+0.25 slip = 10.25*2 + 0.74 = 21.24 ; floor(300/21.24) = 14
    assert intent.quantity == 14
    assert intent.approved_risk_dollars == pytest.approx(14 * 21.24)
    assert intent.approved_risk_dollars <= 300
    assert intent.side == TradeSide.LONG
    assert intent.stop_price == 19990.0
    assert intent.intent_id == "intent-setup-1"  # deterministic
    assert intent.created_at == DECISION_TIME


def test_decision_is_deterministic():
    a = _engine().evaluate(make_setup()).intent
    b = _engine().evaluate(make_setup()).intent
    assert a.to_dict() == b.to_dict()


def test_percent_risk_mode_uses_account_size():
    # 0.2% of 50,000 = $100 max risk -> floor(100 / 21.24) = 4 contracts
    decision = _engine(risk_mode="percent", risk_percent=0.2, account_size=50000.0).evaluate(make_setup())
    assert decision.approved
    assert decision.intent.quantity == 4
    assert decision.intent.approved_risk_dollars <= 100.0


def test_no_trade_setup_is_rejected():
    d = _engine().evaluate(make_setup(decision="NO_TRADE"))
    assert not d.approved and d.intent is None


@pytest.mark.parametrize("field", ["proposed_side", "proposed_entry", "proposed_stop"])
def test_missing_trade_fields_reject(field):
    d = _engine().evaluate(make_setup(**{field: None}))
    assert not d.approved and any("no proposed" in r for r in d.rejection_reasons)


def test_wrong_side_stop_rejected():
    assert not _engine().evaluate(make_setup(proposed_stop=20010.0)).approved  # long, stop above
    short = make_setup(proposed_side=TradeSide.SHORT, proposed_stop=19990.0)
    assert not _engine().evaluate(short).approved


def test_off_tick_prices_rejected_not_rounded():
    d = _engine().evaluate(make_setup(proposed_stop=19990.10))
    assert not d.approved and any("tick" in r for r in d.rejection_reasons)


def test_target_on_wrong_side_rejected():
    bad = make_setup(targets=[Target(type="t", price=19995.0, source="t")])
    assert not _engine().evaluate(bad).approved


def test_single_contract_exceeding_max_risk_rejected():
    d = _engine(risk_dollars=10.0).evaluate(make_setup())
    assert not d.approved and any("sizing" in r.lower() for r in d.rejection_reasons)


def test_es_and_unknown_and_inactive_symbols_rejected():
    assert not _engine().evaluate(make_setup(symbol="ES")).approved
    assert not _engine().evaluate(make_setup(symbol="XYZ")).approved
    assert not _engine().evaluate(make_setup(symbol="NQ")).approved  # not the active symbol


def test_naive_timestamp_rejected():
    d = _engine().evaluate(make_setup(evaluated_at=datetime(2026, 1, 5, 9, 30)))
    assert not d.approved


def test_stale_daily_state_for_other_day_rejected():
    other_day = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
    assert not _engine().evaluate(make_setup(evaluated_at=other_day)).approved


def test_daily_loss_limit_blocks_further_trading():
    config = load_config()
    state = make_daily_state(config)
    engine = RiskEngine(config, state)
    assert engine.evaluate(make_setup()).approved
    state.record_trade_result(-1300.0)  # exceeds max_daily_loss (1200)
    d = engine.evaluate(make_setup(setup_id="setup-2"))
    assert not d.approved and any("lockout" in r.lower() for r in d.rejection_reasons)


def test_max_losses_lockout_blocks_trading():
    config = load_config()
    state = make_daily_state(config)
    engine = RiskEngine(config, state)
    state.record_trade_result(-50.0)
    state.record_trade_result(-50.0)
    assert not engine.evaluate(make_setup()).approved


def test_all_independent_reasons_reported_together():
    d = _engine().evaluate(make_setup(decision="NO_TRADE", symbol="ES"))
    assert len(d.rejection_reasons) >= 2
