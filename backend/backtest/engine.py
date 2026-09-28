"""
Backtest engine skeleton (Step 10 of the development process).

Not implemented yet -- this file establishes the intended shape so later
steps (market-data ingestion, sessions/liquidity, HTF bias, concept
detectors, Strategy V1, risk engine, PaperBroker) have a clear integration
point, without pretending backtesting works before those pieces exist.

Design constraints from the spec that this must respect once implemented:
  - Input: 1M OHLCV minimum, aggregated up to 5M/15M/1H/4H/Daily.
  - No lookahead bias: an HTF candle isn't "closed" until its actual close time.
  - Process data sequentially, as if seeing it in real time.
  - Reuse the same Strategy / RiskEngine / TradeManager / DecisionLogger /
    Analytics that ForwardTestEngine uses -- only the data feed differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config.schema import AppConfig
from backend.decision_log.decisions import DecisionLogger
from backend.strategies.base import Strategy


@dataclass
class BacktestConfig:
    data_path: Path
    symbol: str
    start: Optional[str] = None
    end: Optional[str] = None


class BacktestEngine:
    def __init__(self, app_config: AppConfig, strategy: Strategy, decision_logger: DecisionLogger) -> None:
        self.app_config = app_config
        self.strategy = strategy
        self.decision_logger = decision_logger

    def run(self, backtest_config: BacktestConfig) -> None:
        raise NotImplementedError(
            "BacktestEngine.run is not implemented yet. Requires: "
            "historical data ingestion (backend/data/historical), timeframe "
            "aggregation (backend/market/candles), and the concept detectors "
            "in backend/concepts/ (Steps 2-6) before sequential replay can "
            "produce meaningful signals."
        )
