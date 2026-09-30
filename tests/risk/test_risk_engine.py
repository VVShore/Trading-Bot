from datetime import datetime, timezone

import pytest

from backend.core.enums import TradeSide
from backend.core.models.order_intent import OrderIntent
from backend.core.models.target import Target
from tests.helpers import DECISION_TIME, make_engine, make_setup


def test_valid_setup_is_approved_with_locked_formula():
    engine, _, _ = make_engine()
    decision = engine.evaluate(make_setup())
    assert decision.approved is True
    intent = decision.intent
    assert isinstance(intent, OrderIntent) and intent.is_authentic
    # policy lock 1: N = floor(300 / (10 pts * $2)) = 15 ; no commission/slippage in the ceiling
    assert intent.quantity == 15
    assert intent.approved_risk_dollars == pytest.approx(300.0)
    assert intent.risk_per_contract_dollars == pytest.approx(20.0)
    assert intent.side == TradeSide.LONG and intent.stop_price == 19990.0
    assert intent.intent_id == "intent-setup-1"  # deterministic
    assert intent.created_at == DECISION_TIME


def test_decision_is_deterministic():
    a = make_engine()[0].evaluate(make_setup()).intent
    b = make_engine()[0].evaluate(make_setup()).intent
    assert a.to_dict() == b.to_dict()


def test_percent_risk_mode_uses_account_size():
    engine, _, _ = make_engine(risk_mode="percent", risk_percent=0.5, account_size=50000.0)  # $250
    d = engine.evaluate(make_setup())
    assert d.approved and d.intent.quantity == 12  # floor(250 / 20)
    assert d.intent.approved_risk_dollars <= 250.0


def test_no_trade_setup_is_rejected():
    d = make_engine()[0].evaluate(make_setup(decision="NO_TRADE"))
    assert not d.approved and d.intent is None


@pytest.mark.parametrize("field", ["proposed_side", "proposed_entry", "proposed_stop"])
def test_missing_trade_fields_reject(field):
    d = make_engine()[0].evaluate(make_setup(**{field: None}))
    assert not d.approved and any("no proposed" in r for r in d.rejection_reasons)


def test_wrong_side_stop_rejected():
    engine, _, _ = make_engine()
    assert not engine.evaluate(make_setup(proposed_stop=20010.0)).approved
    assert not engine.evaluate(make_setup(proposed_side=TradeSide.SHORT, proposed_stop=19990.0)).approved


# --- policy lock 2: deterministic rounding, done before sizing ---------------------------

def test_off_tick_entry_rounds_toward_market_and_needs_market_price():
    engine, _, _ = make_engine()
    setup = make_setup(proposed_entry=20000.125, proposed_stop=19990.0)
    missing = engine.evaluate(setup)
    assert not missing.approved and any("market price" in r for r in missing.rejection_reasons)

    up = engine.evaluate(setup, market_price=20005.0)
    assert up.approved and up.intent.entry_price == 20000.25
    assert any("entry" in a for a in up.adjustments)

    engine2, _, _ = make_engine()
    down = engine2.evaluate(make_setup(setup_id="s2", proposed_entry=20000.125, proposed_stop=19990.0), market_price=19995.0)
    assert down.approved and down.intent.entry_price == 20000.0


def test_off_tick_stop_rounds_away_and_sizing_uses_rounded_stop():
    engine, _, _ = make_engine()
    d = engine.evaluate(make_setup(proposed_stop=19990.125))  # long: away from entry = DOWN
    assert d.approved and d.intent.stop_price == 19990.0
    engine2, _, _ = make_engine()
    s = engine2.evaluate(make_setup(setup_id="s", proposed_side=TradeSide.SHORT, proposed_entry=20000.0,
                                    proposed_stop=20010.125, targets=[]))  # short: away = UP
    assert s.approved and s.intent.stop_price == 20010.25
    # risk uses the widened stop: 10.25 pts * $2 = $20.50/contract -> floor(300/20.5) = 14
    assert s.intent.quantity == 14 and s.intent.approved_risk_dollars <= 300.0


def test_off_tick_target_rounds_toward_entry():
    engine, _, _ = make_engine()
    d = engine.evaluate(make_setup(targets=[Target(type="t", price=20030.125, source="t")]))
    assert d.approved and d.intent.targets[0].price == 20030.0
    engine2, _, _ = make_engine()
    s = engine2.evaluate(make_setup(setup_id="s", proposed_side=TradeSide.SHORT, proposed_entry=20000.0,
                                    proposed_stop=20010.0, targets=[Target(type="t", price=19970.125, source="t")]))
    assert s.approved and s.intent.targets[0].price == 19970.25


def test_target_on_wrong_side_or_collapsing_onto_entry_rejected():
    engine, _, _ = make_engine()
    assert not engine.evaluate(make_setup(targets=[Target(type="t", price=19995.0, source="t")])).approved
    engine2, _, _ = make_engine()
    assert not engine2.evaluate(make_setup(setup_id="s", targets=[Target(type="t", price=20000.10, source="t")])).approved


def test_single_contract_exceeding_max_risk_rejected():
    engine, _, _ = make_engine(risk_dollars=10.0)
    d = engine.evaluate(make_setup())
    assert not d.approved and any("sizing" in r.lower() for r in d.rejection_reasons)


def test_es_and_unknown_and_inactive_symbols_rejected():
    engine, _, _ = make_engine()
    assert not engine.evaluate(make_setup(symbol="ES")).approved
    assert not engine.evaluate(make_setup(symbol="XYZ")).approved
    assert not engine.evaluate(make_setup(symbol="NQ")).approved  # decision 5: MNQ only


def test_naive_timestamp_and_stale_day_rejected():
    engine, _, _ = make_engine()
    assert not engine.evaluate(make_setup(evaluated_at=datetime(2026, 1, 5, 9, 30))).approved
    other_day = datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)
    assert not engine.evaluate(make_setup(evaluated_at=other_day)).approved


def test_all_independent_reasons_reported_together():
    d = make_engine()[0].evaluate(make_setup(decision="NO_TRADE", symbol="ES"))
    assert len(d.rejection_reasons) >= 2


# --- policy lock 3: concurrency + daily trade cap --------------------------------------

def test_max_one_concurrent_position_blocks_when_broker_has_one_open():
    engine, _, _ = make_engine(open_positions=lambda: 1)
    d = engine.evaluate(make_setup())
    assert not d.approved and any("concurrent" in r.lower() for r in d.rejection_reasons)


def test_unsettled_intent_counts_toward_concurrency_until_settled():
    engine, _, _ = make_engine()
    first = engine.evaluate(make_setup(setup_id="a"))
    assert first.approved
    assert not engine.evaluate(make_setup(setup_id="b")).approved  # first not yet filled/settled
    engine.settle_intent(first.intent.intent_id)
    assert engine.evaluate(make_setup(setup_id="c")).approved


def test_daily_trade_cap_is_six_and_locks_out():
    engine, state, config = make_engine()
    assert config.risk.max_trades_per_day == 6
    for _ in range(5):
        state.record_trade_result(10.0)
    assert engine.evaluate(make_setup(setup_id="t6")).approved
    engine.settle_intent("intent-t6")
    state.record_trade_result(10.0)  # 6th completed trade
    d = engine.evaluate(make_setup(setup_id="t7"))
    assert not d.approved and any("Max trades/day" in r for r in d.rejection_reasons)


def test_backtest_override_lifts_only_the_trade_cap():
    engine, state, _ = make_engine(backtest_override_trade_frequency=True)
    for _ in range(8):
        state.record_trade_result(10.0)
    assert engine.evaluate(make_setup()).approved


# --- policy lock 4: daily loss budget ---------------------------------------------------

def test_lockout_when_cumulative_loss_equals_the_limit():
    engine, state, _ = make_engine()
    for pnl in (-700.0, -700.0, -600.0):  # exactly -2000
        state.record_trade_result(pnl)
    d = engine.evaluate(make_setup())
    assert not d.approved and any("Max daily loss" in r for r in d.rejection_reasons)


def test_lockout_when_cumulative_loss_exceeds_the_limit():
    engine, state, _ = make_engine()
    state.record_trade_result(-2100.0)
    assert not engine.evaluate(make_setup()).approved


def test_lockout_when_remaining_budget_cannot_cover_a_full_trade():
    engine, state, _ = make_engine()
    state.record_trade_result(-1750.0)  # $250 left < $300 per-trade allocation
    d = engine.evaluate(make_setup())
    assert not d.approved and any("cannot cover" in r for r in d.rejection_reasons)


def test_budget_that_still_covers_one_trade_does_not_lock():
    engine, state, _ = make_engine()
    state.record_trade_result(-1700.0)  # exactly $300 left
    assert engine.evaluate(make_setup()).approved


def test_consecutive_losses_do_not_lock_out_while_budget_remains():
    engine, state, _ = make_engine()
    for _ in range(3):
        state.record_trade_result(-300.0)  # -900: $1,100 budget left, still >= one $300 allocation
    assert engine.evaluate(make_setup()).approved


def test_costs_are_not_part_of_the_sizing_ceiling():
    # Owner resolution 2: pure formula. 10 pts x $2 = $20/contract -> 15 contracts at a $300 ceiling,
    # even though commission + slippage would make one stop-out cost a little more than $300.
    decision = make_engine()[0].evaluate(make_setup())
    assert decision.intent.quantity == 15
    assert decision.intent.approved_risk_dollars == pytest.approx(300.0)


def test_pause_trading_flag_blocks_everything():
    engine, _, _ = make_engine(pause_trading=True)
    d = engine.evaluate(make_setup())
    assert not d.approved and any("paused" in r for r in d.rejection_reasons)


# --- policy lock 1: re-size or reject at fill ------------------------------------------

def _approved_intent(engine):
    return engine.evaluate(make_setup()).intent  # entry 20000, stop 19990, qty 15


def test_fill_at_planned_price_keeps_the_intent():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    d = engine.reconcile_fill(intent, 20000.0)
    assert d.approved and d.intent is intent


def test_adverse_gap_resizes_down_to_hold_the_ceiling():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    d = engine.reconcile_fill(intent, 20005.0)  # stop distance 10 -> 15 pts ; floor(300/30) = 10
    assert d.approved and d.intent.quantity == 10
    assert d.intent.approved_risk_dollars <= 300.0
    assert d.intent.entry_price == 20005.0 and d.intent.is_authentic
    assert d.adjustments and "Re-sized at fill" in d.adjustments[0]


def test_favorable_gap_never_sizes_up():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    d = engine.reconcile_fill(intent, 19995.0)  # distance shrinks to 5 pts
    assert d.approved and d.intent.quantity == 15


def test_gap_too_large_for_any_size_is_rejected_and_settled():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    d = engine.reconcile_fill(intent, 20200.0)  # 210 pts * $2 = $420 for ONE contract > $300 ceiling
    assert not d.approved
    assert engine.evaluate(make_setup(setup_id="next")).approved  # settled -> slot free again


def test_fill_through_the_stop_is_rejected():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    d = engine.reconcile_fill(intent, 19985.0)
    assert not d.approved and any("through the stop" in r for r in d.rejection_reasons)


def test_reconcile_requires_an_outstanding_authentic_intent():
    engine, _, _ = make_engine()
    intent = _approved_intent(engine)
    engine.settle_intent(intent.intent_id)
    assert not engine.reconcile_fill(intent, 20000.0).approved
    assert not engine.reconcile_fill(make_setup(), 20000.0).approved  # not an intent
