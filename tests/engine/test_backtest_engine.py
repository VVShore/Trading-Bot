"""BacktestEngine: same orchestrator, multi-day replay, reproducible manifest."""
import csv
import hashlib
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.config.loader import load_config
from backend.core.enums import StrategyName, TradeSide
from backend.core.models.setup import TradeSetup
from backend.core.models.target import Target
from backend.data.normalizer import BarFieldMap, BarNormalizer
from backend.data.provider import CsvBarProvider
from backend.engine.backtest import ENGINE_VERSION, BacktestConfig, BacktestEngine, git_info
from backend.pipeline import build_paper_orchestrator
from backend.strategies.base import MarketContext, Strategy
from tests.helpers import NY

LONG = dict(side=TradeSide.LONG, entry=20000.0, stop=19990.0, target=20030.0)


class OnceAt(Strategy):
    """Emits the long setup when the context as_of hits one of the given (day, hh, mm) times."""
    name, version = "ONCE_AT", "ONCE-AT-1"

    def __init__(self, config, times):
        super().__init__(config)
        self.times = {datetime(2026, 1, d, h, m, tzinfo=NY) for d, h, m in times}

    def evaluate(self, context: MarketContext) -> TradeSetup:
        common = dict(setup_id=f"x-{context.as_of.strftime('%d-%H%M')}", strategy=StrategyName.NY_AM_HTF_CONTINUATION_V1,
                      strategy_version=self.version, symbol=context.symbol, evaluated_at=context.as_of)
        if context.as_of not in self.times:
            return TradeSetup(**common, decision="NO_TRADE", reason="no")
        return TradeSetup(**common, proposed_side=LONG["side"], proposed_entry=LONG["entry"], proposed_stop=LONG["stop"],
                          targets=[Target(type="t", price=LONG["target"], source="t")], decision="TRADE")


def write_csv(path: Path, days=(5, 6), winner=True):
    """Per day: 09:00-09:34 flat, 09:35 entry bar, 09:36 exit bar (target hit, or stop hit), 09:37 flat."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for d in days:
            start = datetime(2026, 1, d, 9, 0)
            for k in range(35):
                w.writerow([(start + timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M:%S"), 20000, 20000.5, 19999.5, 20000, 10])
            w.writerow([f"2026-01-{d:02d} 09:35:00", 20000, 20010, 19995, 20008, 10])
            w.writerow([f"2026-01-{d:02d} 09:36:00", 20008, 20031 if winner else 20008, 20001 if winner else 19985, 20030 if winner else 19990, 10])
            w.writerow([f"2026-01-{d:02d} 09:37:00", 20000, 20000.5, 19999.5, 20000, 10])


def engine(config=None, times=((5, 9, 35), (6, 9, 35))):
    return BacktestEngine(config or load_config(), lambda cfg: OnceAt(cfg, times))


def cfg(data, out, **kw):
    return BacktestConfig(data_files=(Path(data),), output_dir=Path(out), source_tz="America/New_York", **kw)


# ----------------------------------------------------------------------------------------------
def test_run_replays_multiple_days_and_writes_all_artifacts(tmp_path):
    data = tmp_path / "mnq.csv"
    write_csv(data)
    result = engine().run(cfg(data, tmp_path / "run1"))

    assert len(result.trades) == 2 and all(t.pnl_dollars == pytest.approx(870.3) for t in result.trades)
    assert [d["trading_day"] for d in result.daily] == ["2026-01-05", "2026-01-06"]
    assert [d["trades_taken"] for d in result.daily] == [1, 1]
    for p in (result.manifest_path, result.decision_log_path, result.trades_path):
        assert p.exists() and p.parent == tmp_path / "run1"
    assert result.steps == 2 * 38


def test_engine_produces_exactly_what_the_orchestrator_does(tmp_path):
    """No separate backtest logic: a hand-wired orchestrator over the same file gives identical trades."""
    data = tmp_path / "mnq.csv"
    write_csv(data)
    via_engine = engine().run(cfg(data, tmp_path / "a"))

    config = load_config()
    orch, broker, _ = build_paper_orchestrator(
        config=config, provider=CsvBarProvider(data),
        normalizer=BarNormalizer("MNQ", source_tz="America/New_York"),
        strategy=OnceAt(config, ((5, 9, 35), (6, 9, 35))), log_path=tmp_path / "b.ndjson", trade_store_path=tmp_path / "b_trades.ndjson")
    list(orch.run())
    assert [t.model_dump() for t in broker.closed_trades()] == [t.model_dump() for t in via_engine.trades]
    assert (tmp_path / "b.ndjson").read_text() == via_engine.decision_log_path.read_text()


def test_manifest_contents(tmp_path):
    data = tmp_path / "mnq.csv"
    write_csv(data)
    m = engine().run(cfg(data, tmp_path / "run")).manifest
    on_disk = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert on_disk == m

    assert m["engine"] == {"name": "BacktestEngine", "version": ENGINE_VERSION}
    sha, dirty = git_info()
    assert m["code"] == {"git_sha": sha, "git_dirty": dirty}
    if sha:
        assert sha == subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                     cwd=Path(__file__).resolve().parents[2]).stdout.strip()
    assert len(m["config"]["sha256"]) == 64 and m["config"]["config_version"] == "0.1.0"
    assert m["strategy"] == {"name": "ONCE_AT", "version": "ONCE-AT-1"}

    d = m["data"]
    assert d["files"] == [{"name": "mnq.csv", "sha256": hashlib.sha256(data.read_bytes()).hexdigest(), "bytes": data.stat().st_size}]
    assert d["combined_sha256"] == hashlib.sha256(d["files"][0]["sha256"].encode()).hexdigest()
    assert d["first_candle_open"] == "2026-01-05T09:00:00-05:00" and d["last_candle_open"] == "2026-01-06T09:37:00-05:00"
    assert (d["first_trading_day"], d["last_trading_day"], d["trading_days"]) == ("2026-01-05", "2026-01-06", 2)
    assert (d["rows_read"], d["candles_ingested"]) == (76, 76)
    assert m["results"]["trades"] == 2 and m["results"]["net_pnl_dollars"] == pytest.approx(1740.6)
    assert m["results"]["open_positions_at_end"] == [] and len(m["results"]["days"]) == 2
    assert m["artifacts"]["decision_log.ndjson"]["sha256"] == hashlib.sha256((tmp_path / "run" / "decision_log.ndjson").read_bytes()).hexdigest()
    assert str(tmp_path) not in json.dumps(m)           # no absolute paths: manifests are portable


def test_same_inputs_give_a_byte_identical_manifest_and_artifacts(tmp_path):
    data = tmp_path / "mnq.csv"
    write_csv(data)
    a = engine().run(cfg(data, tmp_path / "a"))
    b = engine().run(cfg(data, tmp_path / "b"))
    assert a.manifest_path.read_bytes() == b.manifest_path.read_bytes()
    assert a.decision_log_path.read_bytes() == b.decision_log_path.read_bytes()
    assert a.trades_path.read_bytes() == b.trades_path.read_bytes()


def test_changing_data_or_config_changes_the_manifest_hashes(tmp_path):
    data, other = tmp_path / "mnq.csv", tmp_path / "mnq2.csv"
    write_csv(data)
    write_csv(other, winner=False)
    base = engine().run(cfg(data, tmp_path / "a")).manifest
    changed_data = engine().run(cfg(other, tmp_path / "b")).manifest
    assert changed_data["data"]["files"][0]["sha256"] != base["data"]["files"][0]["sha256"]
    assert changed_data["data"]["combined_sha256"] != base["data"]["combined_sha256"]
    assert changed_data["results"]["net_pnl_dollars"] < 0 < base["results"]["net_pnl_dollars"]

    tweaked = load_config()
    tweaked = tweaked.model_copy(update={"risk": tweaked.risk.model_copy(update={"risk_dollars": 250.0})})
    changed_cfg = engine(tweaked).run(cfg(data, tmp_path / "c")).manifest
    assert changed_cfg["config"]["sha256"] != base["config"]["sha256"]
    assert changed_cfg["data"] == base["data"]


def test_multiple_data_files_are_replayed_in_order_and_each_hashed(tmp_path):
    f1, f2 = tmp_path / "jan05.csv", tmp_path / "jan06.csv"
    write_csv(f1, days=(5,))
    write_csv(f2, days=(6,))
    res = engine().run(BacktestConfig(data_files=(f1, f2), output_dir=tmp_path / "run", source_tz="America/New_York"))
    files = res.manifest["data"]["files"]
    assert [f["name"] for f in files] == ["jan05.csv", "jan06.csv"] and files[0]["sha256"] != files[1]["sha256"]
    assert len(res.trades) == 2 and res.manifest["data"]["trading_days"] == 2


def test_positions_open_when_data_ends_are_reported_not_hidden(tmp_path):
    data = tmp_path / "mnq.csv"
    with open(data, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for k in range(36):
            w.writerow([(datetime(2026, 1, 5, 9, 0) + timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M:%S"), 20000, 20000.5, 19999.5, 20000, 10])
    m = engine(times=((5, 9, 35),)).run(cfg(data, tmp_path / "run")).manifest
    assert m["results"]["open_positions_at_end"] == [{"symbol": "MNQ", "side": "long", "quantity": 15}]
    assert m["results"]["trades"] == 0


def test_custom_csv_layout_and_close_stamped_bars(tmp_path):
    data = tmp_path / "vendor.csv"
    with open(data, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "o", "h", "l", "c", "vol"])
        for k in range(1, 6):   # stamped by CLOSE time: first bar covers 09:00-09:01
            w.writerow([(datetime(2026, 1, 5, 9, 0) + timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M:%S"), 1, 2, 1, 2, 3])
    fm = BarFieldMap(timestamp="time", open="o", high="h", low="l", close="c", volume="vol")
    m = engine(times=()).run(BacktestConfig(data_files=(data,), output_dir=tmp_path / "r", source_tz="America/New_York",
                                            field_map=fm, timestamp_marks="close")).manifest
    assert m["data"]["first_candle_open"] == "2026-01-05T09:00:00-05:00" and m["data"]["format"]["timestamp_marks"] == "close"


def test_refuses_to_overwrite_a_previous_run_and_validates_inputs(tmp_path):
    data = tmp_path / "mnq.csv"
    write_csv(data, days=(5,))
    engine().run(cfg(data, tmp_path / "run"))
    with pytest.raises(FileExistsError):
        engine().run(cfg(data, tmp_path / "run"))
    with pytest.raises(FileNotFoundError):
        engine().run(cfg(tmp_path / "missing.csv", tmp_path / "x"))
    with pytest.raises(ValueError):
        engine().run(BacktestConfig(data_files=(), output_dir=tmp_path / "y"))


def test_engine_refuses_non_paper_environments():
    c = load_config()
    live_like = c.model_copy(update={"execution": c.execution.model_copy(update={"broker_environment": "tradovate_demo"})})
    with pytest.raises(ValueError, match="paper"):
        BacktestEngine(live_like, lambda cfg: None)


def test_legacy_import_path_still_resolves():
    from backend.backtest.engine import BacktestEngine as Legacy
    assert Legacy is BacktestEngine
