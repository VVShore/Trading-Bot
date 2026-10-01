"""
BacktestEngine: replays historical 1M bars through the SAME PipelineOrchestrator that paper/live
will use (same normalizer, aggregator, detectors, strategy, RiskEngine, PaperBroker brackets,
flatten rule, day rollover, decision log). There is no separate backtest strategy or fill logic.

A run writes three artifacts into `output_dir` (and refuses to overwrite existing ones):
  decision_log.ndjson  the complete decision trail
  trades.ndjson        every closed TradeRecord
  manifest.json        what is needed to reproduce and compare the run: git SHA, config hash,
                       data file hashes, date ranges, engine version, artifact hashes, result summary.

The manifest contains no wall-clock time and no absolute paths, so the same code + config + data
always produce a byte-identical manifest and identical artifact hashes.

Positions still open when the data ends are NOT force-closed (no candle exists to close them at);
they are reported in the manifest under `results.open_positions_at_end`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from backend.config.schema import AppConfig
from backend.core.models.trade import TradeRecord
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.provider import ChainedProvider, CsvBarProvider
from backend.detectors.base import Detector
from backend.pipeline.orchestrator import build_paper_orchestrator
from backend.strategies.base import Strategy

ENGINE_VERSION = "0.1.0"
MANIFEST_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_LOG_NAME = "decision_log.ndjson"
TRADES_NAME = "trades.ndjson"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class BacktestConfig:
    data_files: tuple[Path, ...]
    output_dir: Path
    symbol: str = "MNQ"
    field_map: BarFieldMap = field(default_factory=BarFieldMap)
    source_tz: str = "UTC"              # zone of NAIVE timestamps in the CSV
    timestamp_marks: str = "open"       # does the vendor stamp a bar by its "open" or "close" time?
    assume_closed: bool = True
    delimiter: str = ","


@dataclass
class BacktestResult:
    manifest: dict[str, Any]
    manifest_path: Path
    decision_log_path: Path
    trades_path: Path
    trades: list[TradeRecord]
    daily: list[dict[str, Any]]
    steps: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(config: AppConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def git_info(root: Path = REPO_ROOT) -> tuple[Optional[str], Optional[bool]]:
    """(HEAD sha, has uncommitted changes to tracked files); (None, None) when git is unavailable."""
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True).stdout
    try:
        return run("rev-parse", "HEAD").strip(), bool(run("status", "--porcelain", "--untracked-files=no").strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


class BacktestEngine:
    def __init__(
        self,
        app_config: AppConfig,
        strategy_factory: Callable[[AppConfig], Strategy],
        detectors: Optional[Sequence[Detector]] = None,
    ) -> None:
        if app_config.execution.broker_environment != "paper":
            raise ValueError("BacktestEngine runs on the PaperBroker only (broker_environment must be 'paper').")
        self.app_config = app_config
        self.strategy_factory = strategy_factory
        self.detectors = detectors

    def run(self, backtest_config: BacktestConfig) -> BacktestResult:
        cfg = backtest_config
        files = tuple(Path(p) for p in cfg.data_files)
        if not files:
            raise ValueError("BacktestConfig.data_files is empty.")
        missing = [str(p) for p in files if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"Data file(s) not found: {missing}")

        out = Path(cfg.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        log_path, trades_path, manifest_path = out / DECISION_LOG_NAME, out / TRADES_NAME, out / MANIFEST_NAME
        existing = [p.name for p in (log_path, trades_path, manifest_path) if p.exists()]
        if existing:
            raise FileExistsError(f"Refusing to overwrite a previous run's artifacts in {out}: {existing}")

        data_hashes = [sha256_file(p) for p in files]  # hashed BEFORE reading, from the same bytes the run will see
        strategy = self.strategy_factory(self.app_config)
        normalizer = BarNormalizer(
            cfg.symbol, field_map=cfg.field_map, source_tz=cfg.source_tz,
            timestamp_marks=cfg.timestamp_marks, assume_closed=cfg.assume_closed,  # type: ignore[arg-type]
        )
        provider = ChainedProvider([CsvBarProvider(p, delimiter=cfg.delimiter) for p in files])
        orchestrator, broker, _daily = build_paper_orchestrator(
            config=self.app_config, provider=provider, normalizer=normalizer, strategy=strategy,
            log_path=log_path, trade_store_path=trades_path, detectors=self.detectors,
        )

        steps = 0
        first: Optional[datetime] = None
        last: Optional[datetime] = None
        for step in orchestrator.run():  # run() also cancels any intent still pending when the data ends
            steps += 1
            if step.candle is not None:
                first = first or step.candle.open_time
                last = step.candle.open_time

        trades = broker.closed_trades()
        daily = orchestrator.day_summaries()
        stats = orchestrator.pipeline_stats
        open_at_end = [
            {"symbol": t.symbol, "side": t.side.value, "quantity": t.quantity} for t in broker.open_trades()
        ]
        manifest = self._manifest(
            cfg, strategy, files, data_hashes, first, last, stats, trades, daily, open_at_end, log_path, trades_path
        )
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return BacktestResult(manifest, manifest_path, log_path, trades_path, trades, daily, steps)

    # ------------------------------------------------------------------

    def _manifest(self, cfg, strategy, files, data_hashes, first, last, stats, trades, daily, open_at_end,
                  log_path: Path, trades_path: Path) -> dict[str, Any]:
        sha, dirty = git_info()
        pnls = [t.pnl_dollars or 0.0 for t in trades]
        combined = hashlib.sha256("".join(data_hashes).encode()).hexdigest()
        days_with_data = [d["trading_day"] for d in daily]
        return {
            "manifest_version": MANIFEST_VERSION,
            "engine": {"name": "BacktestEngine", "version": ENGINE_VERSION},
            "code": {"git_sha": sha, "git_dirty": dirty},
            "config": {"sha256": config_hash(self.app_config), "config_version": self.app_config.config_version},
            "strategy": {"name": getattr(strategy, "name", type(strategy).__name__),
                         "version": getattr(strategy, "version", "unknown")},
            "symbol": cfg.symbol,
            "data": {
                "files": [{"name": p.name, "sha256": h, "bytes": p.stat().st_size} for p, h in zip(files, data_hashes)],
                "combined_sha256": combined,
                "format": {**dataclasses.asdict(cfg.field_map), "source_tz": cfg.source_tz,
                           "timestamp_marks": cfg.timestamp_marks, "assume_closed": cfg.assume_closed,
                           "delimiter": cfg.delimiter},
                "first_candle_open": first.isoformat() if first else None,
                "last_candle_open": last.isoformat() if last else None,
                "first_trading_day": days_with_data[0] if days_with_data else None,
                "last_trading_day": days_with_data[-1] if days_with_data else None,
                "trading_days": len(days_with_data),
                "rows_read": stats.records_in,
                "candles_ingested": stats.candles_ingested,
                "developing_rows_skipped": stats.developing_skipped,
                "halt_rows_skipped": stats.halt_skipped,
            },
            "results": {
                "trades": len(trades),
                "wins": sum(1 for x in pnls if x > 0),
                "losses": sum(1 for x in pnls if x <= 0),
                "net_pnl_dollars": round(sum(pnls), 6),
                "open_positions_at_end": open_at_end,
                "days": daily,
            },
            "artifacts": {
                DECISION_LOG_NAME: {"sha256": sha256_file(log_path) if log_path.exists() else None},
                TRADES_NAME: {"sha256": sha256_file(trades_path) if trades_path.exists() else None},
            },
        }
