from backend.risk.position_sizing import calculate_position_size, resolve_risk_dollars


def test_basic_position_size():
    # MNQ point value $2, stop distance 20 points -> $40/contract risk
    result = calculate_position_size(
        entry_price=20000.0,
        stop_price=19980.0,
        point_value=2.0,
        max_risk_dollars=300.0,
    )
    assert result.accepted is True
    assert result.risk_per_contract_dollars == 40.0
    assert result.contracts == 7  # floor(300/40) = 7
    assert result.total_risk_dollars == 280.0


def test_position_size_never_exceeds_max_risk_due_to_rounding():
    result = calculate_position_size(
        entry_price=20000.0,
        stop_price=19983.0,
        point_value=2.0,
        max_risk_dollars=300.0,
    )
    assert result.total_risk_dollars <= 300.0


def test_single_contract_exceeding_max_risk_is_rejected():
    result = calculate_position_size(
        entry_price=20000.0,
        stop_price=19000.0,  # 1000 points * $2 = $2000/contract
        point_value=2.0,
        max_risk_dollars=300.0,
    )
    assert result.accepted is False
    assert result.contracts == 0
    assert "exceeds max permitted risk" in result.rejection_reason


def test_equal_entry_and_stop_rejected():
    result = calculate_position_size(
        entry_price=20000.0,
        stop_price=20000.0,
        point_value=2.0,
        max_risk_dollars=300.0,
    )
    assert result.accepted is False


def test_resolve_risk_dollars_dollar_mode():
    assert resolve_risk_dollars("dollar", risk_dollars=300, risk_percent=1.0, account_size=50000) == 300


def test_resolve_risk_dollars_percent_mode():
    assert resolve_risk_dollars("percent", risk_dollars=300, risk_percent=1.0, account_size=50000) == 500.0
