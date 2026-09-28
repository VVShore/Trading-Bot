"""
Minimal application entrypoint.

Per FIRST TASK requirement #11: "Create a minimal working application that
starts successfully." This does NOT run a backtest or connect to a broker.
It:

  1. Loads and validates configuration.
  2. Confirms the live-trading guard is in the expected (disabled) state.
  3. Constructs Strategy V1 and evaluates it once against an empty/placeholder
     MarketContext, to prove the strategy -> TradeSetup -> DecisionLogger
     pipeline is wired correctly end to end.
  4. Prints a summary.

Run with: python main.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.config.loader import load_config
from backend.decision_log.decisions import DecisionLogger
from backend.execution.base import LIVE_TRADING_ENABLED
from backend.strategies.base import MarketContext
from backend.strategies.ny_continuation_v1 import NyAmHtfContinuationV1


def main() -> None:
    print("=" * 70)
    print("Trading Research & Forward-Test Platform -- minimal bootstrap")
    print("=" * 70)

    config = load_config()
    print(f"[OK] Config loaded and validated (config_version={config.config_version})")

    assert LIVE_TRADING_ENABLED is False, "LIVE_TRADING_ENABLED guard must be False."
    assert config.execution.live_trading_enabled is False, "config.execution.live_trading_enabled must be False."
    print(f"[OK] Live trading guard confirmed disabled (broker_environment={config.execution.broker_environment})")

    strategy = NyAmHtfContinuationV1(config=config)
    context = MarketContext(
        as_of=datetime.now(timezone.utc),
        symbol=config.execution.active_symbol,
    )
    setup = strategy.evaluate(context)
    print(f"[OK] Strategy '{strategy.name}' v{strategy.version} evaluated -> decision={setup.decision}")

    log_path = Path("data/decision_log.ndjson")
    logger = DecisionLogger(log_path)
    logger.record(setup)
    print(f"[OK] Decision recorded to {log_path}")

    print("-" * 70)
    print("Bootstrap complete. No market data connected, no orders placed.")
    print("Next steps: implement market-data ingestion (Step 2) and")
    print("sessions/liquidity (Step 3). See docs/ARCHITECTURE.md.")
    print("-" * 70)


if __name__ == "__main__":
    main()
