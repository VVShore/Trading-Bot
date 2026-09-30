from datetime import datetime, timedelta

import pytest

from backend.config.loader import load_config
from backend.context.builder import MarketContextBuilder
from backend.core.enums import Timeframe
from backend.detectors import (
    Detector,
    DetectorInput,
    DetectorResult,
    DetectorStatus,
    LookaheadError,
    PlaceholderDetector,
    default_detectors,
)
from backend.market.candles.aggregator import TimeframeAggregator
from tests.helpers import NY, m1

AS_OF = datetime(2026, 1, 5, 9, 35, tzinfo=NY)


def _input(**candles):
    return DetectorInput(symbol="MNQ", as_of=AS_OF, candles=candles)


def test_detector_is_abstract():
    with pytest.raises(TypeError):
        Detector()  # type: ignore[abstract]


def test_placeholder_defaults_to_unknown():
    r = PlaceholderDetector("x").detect(_input(**{"1m": [m1(2026, 1, 5, 9, 34)]}))
    assert r.status == DetectorStatus.UNKNOWN and r.detector == "x" and not r.detected


def test_missing_required_timeframe_is_not_available():
    det = PlaceholderDetector("htf_bias", (Timeframe.H1,))
    assert det.detect(_input(**{"1m": [m1(2026, 1, 5, 9, 34)]})).status == DetectorStatus.NOT_AVAILABLE
    assert det.detect(_input(**{"1h": []})).status == DetectorStatus.NOT_AVAILABLE


def test_a_crashing_detector_becomes_not_available_never_a_signal():
    class Boom(Detector):
        name = "boom"

        def _detect(self, data):
            raise RuntimeError("bad maths")

    r = Boom().detect(_input())
    assert r.status == DetectorStatus.NOT_AVAILABLE and "bad maths" in r.reason


def test_invalid_return_value_is_not_available():
    class Bad(Detector):
        name = "bad"

        def _detect(self, data):
            return "detected"  # type: ignore[return-value]

    assert Bad().detect(_input()).status == DetectorStatus.NOT_AVAILABLE


def test_detector_input_refuses_unclosed_or_future_candles():
    with pytest.raises(LookaheadError):
        _input(**{"1m": [m1(2026, 1, 5, 9, 34, closed=False)]})
    with pytest.raises(LookaheadError):
        _input(**{"1m": [m1(2026, 1, 5, 9, 35)]})  # closes 09:36, after as_of 09:35
    with pytest.raises(ValueError):
        DetectorInput(symbol="MNQ", as_of=datetime(2026, 1, 5, 9, 35), candles={})


def test_default_detectors_are_unique_and_never_positive():
    dets = default_detectors()
    assert len({d.name for d in dets}) == len(dets)
    assert {"htf_bias", "fvg", "ifvg", "ce", "manipulation", "mss", "cisd", "smt", "liquidity"} <= {d.name for d in dets}
    data = _input(**{"1m": [m1(2026, 1, 5, 9, 34)], "1h": []})
    assert all(d.detect(data).status in (DetectorStatus.UNKNOWN, DetectorStatus.NOT_AVAILABLE) for d in dets)


def _fed_aggregator(minutes):
    agg = TimeframeAggregator("MNQ", load_config().session)
    start = datetime(2026, 1, 5, 8, 0, tzinfo=NY)
    for k in range(minutes):
        t = start + timedelta(minutes=k)
        agg.ingest(m1(t.year, t.month, t.day, t.hour, t.minute, o=100.0))
    return agg


def test_builder_wires_detectors_into_the_context():
    agg = _fed_aggregator(95)  # as_of 09:35, one closed 1H bar
    ctx = MarketContextBuilder(agg, detectors=default_detectors()).build()
    assert set(ctx.detections) == {d.name for d in default_detectors()}
    assert ctx.detections["htf_bias"].status == DetectorStatus.UNKNOWN          # a closed 1H bar exists
    assert ctx.detections["manipulation"].status == DetectorStatus.UNKNOWN
    assert all(r.status in (DetectorStatus.UNKNOWN, DetectorStatus.NOT_AVAILABLE) for r in ctx.detections.values())
    assert all(r.as_of == ctx.as_of for r in ctx.detections.values())


def test_htf_bias_is_not_available_before_the_first_hour_closes():
    agg = _fed_aggregator(30)
    ctx = MarketContextBuilder(agg, detectors=default_detectors()).build()
    assert ctx.detections["htf_bias"].status == DetectorStatus.NOT_AVAILABLE


def test_builder_without_detectors_has_empty_detections_and_rejects_duplicate_names():
    agg = _fed_aggregator(5)
    assert MarketContextBuilder(agg).build().detections == {}
    with pytest.raises(ValueError):
        MarketContextBuilder(agg, detectors=[PlaceholderDetector("a"), PlaceholderDetector("a")])


def test_future_real_detectors_flow_through_the_same_wiring():
    class AlwaysDetected(Detector):  # proves the contract supports a positive answer; not a real rule
        name = "demo"

        def _detect(self, data):
            return self._result(data, DetectorStatus.DETECTED, "demo", level=1.0)

    ctx = MarketContextBuilder(_fed_aggregator(5), detectors=[AlwaysDetected()]).build()
    r = ctx.detections["demo"]
    assert isinstance(r, DetectorResult) and r.detected and r.data == {"level": 1.0}
