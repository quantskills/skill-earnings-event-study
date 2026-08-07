"""Event-study self-checks with synthetic planted effects.

All pass → exit 0; any fail → exit 1.
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

from event_study import (
    MIN_EVENTS_HARD,
    PRIMARY_WINDOW,
    average_ar_path,
    build_report,
    car_stats,
    compute_ar,
    load_events,
    load_market,
    load_returns,
    render_html,
    render_json,
    render_text,
    run_study,
)

RNG = np.random.default_rng(20260807)


def _make_calendar(n_days: int = 400, start: str = "2020-01-02") -> pd.DatetimeIndex:
    return pd.bdate_range(start, periods=n_days)


def _synth_panel(
    n_symbols: int,
    n_days: int,
    event_day_index: int,
    jump: float,
    noise: float = 0.01,
    mkt_vol: float = 0.008,
    miss_symbols: set[str] | None = None,
    miss_frac_in_window: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build events / returns / market with optional planted jump on event day."""
    cal = _make_calendar(n_days)
    symbols = [f"S{i:03d}.SH" for i in range(n_symbols)]
    event_date = cal[event_day_index]

    # Market: random walk returns
    mkt_ret = RNG.normal(0, mkt_vol, size=n_days)
    market = pd.DataFrame({"date": cal, "mkt_ret": mkt_ret})

    rows = []
    for sym in symbols:
        for i, d in enumerate(cal):
            eps = RNG.normal(0, noise)
            r = mkt_ret[i] + eps
            if i == event_day_index:
                r += jump
            rows.append({"date": d, "symbol": sym, "ret": r})

    returns = pd.DataFrame(rows)

    # Optional: wipe returns in event window for some symbols (missing-data drop test)
    if miss_symbols and miss_frac_in_window:
        pre, post = 10, 10
        wipe = set(range(event_day_index - pre, event_day_index + post + 1))
        n_wipe = int(miss_frac_in_window * (pre + post + 1))
        wipe_taus = list(wipe)[:n_wipe]
        wipe_dates = set(cal[i] for i in wipe_taus if 0 <= i < n_days)
        mask = returns["symbol"].isin(miss_symbols) & returns["date"].isin(wipe_dates)
        returns.loc[mask, "ret"] = np.nan
        returns = returns.dropna(subset=["ret"])

    events = pd.DataFrame({
        "symbol": symbols,
        "event_date": [event_date] * n_symbols,
        "event_type": ["earnings"] * n_symbols,
    })
    return events, returns, market


def _write_csvs(td: Path, events, returns, market=None):
    e = td / "events.csv"
    r = td / "returns.csv"
    events.to_csv(e, index=False)
    returns.to_csv(r, index=False)
    m = None
    if market is not None:
        m = td / "market.csv"
        market.to_csv(m, index=False)
    return str(e), str(r), str(m) if m else None


def test_planted_positive_jump():
    """Planted +jump on event day → mean CAR[-1,1] significantly > 0."""
    events, returns, market = _synth_panel(60, 350, event_day_index=200, jump=0.04, noise=0.005)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        rep, _, _ = run_study(e, r, m, model="market", pre=10, post=10)
    mean = rep["primary"]["mean_car"]
    p = rep["primary"]["p_value"]
    assert mean > 0.02, f"expected strong positive CAR, got {mean}"
    assert p < 0.05, f"expected significant, p={p}"
    assert rep["primary"]["significant_05"] is True


def test_planted_negative_jump():
    """Planted −jump → CAR < 0 and significant."""
    events, returns, market = _synth_panel(60, 350, event_day_index=200, jump=-0.04, noise=0.005)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        rep, _, _ = run_study(e, r, m, model="market", pre=10, post=10)
    mean = rep["primary"]["mean_car"]
    assert mean < -0.02, f"expected negative CAR, got {mean}"
    assert rep["primary"]["p_value"] < 0.05


def test_no_event_effect_not_significant():
    """Pure noise (jump=0) → primary window not flagged significant at 0.05."""
    events, returns, market = _synth_panel(80, 350, event_day_index=200, jump=0.0, noise=0.012)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        rep, _, _ = run_study(e, r, m, model="market", pre=10, post=10)
    # With noise and no planted effect, p should usually be > 0.05
    assert rep["primary"]["significant_05"] is False, (
        f"noise run flagged significant: p={rep['primary']['p_value']}, "
        f"mean={rep['primary']['mean_car']}"
    )


def test_usable_event_count_guard():
    """N_used < 20 must refuse via ValueError."""
    events, returns, market = _synth_panel(10, 350, event_day_index=200, jump=0.03)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        try:
            run_study(e, r, m, model="market")
        except ValueError as err:
            assert str(MIN_EVENTS_HARD) in str(err) or "refuse" in str(err).lower() or "不足" in str(err) or "<" in str(err)
            return
    raise AssertionError("expected ValueError for N < 20")


def test_estimation_window_excludes_event_day():
    """Mean-adjusted model: estimation window must end before event (no lookahead)."""
    events, returns, market = _synth_panel(40, 400, event_day_index=250, jump=0.02, noise=0.005)
    ar_panel, meta = compute_ar(
        events, returns, market, model="mean", pre=10, post=10,
        est_start=-120, est_end=-21,
    )
    assert meta["n_used"] >= 20
    # For every used event, est_mean is finite and ARs exist; event day tau=0 is not in est
    assert meta["est_end"] < 0
    assert meta["est_end"] <= -21
    # Spot-check: recompute that estimation dates never include event date
    cal = pd.DatetimeIndex(sorted(returns["date"].unique()))
    for eid, g in ar_panel.groupby("event_id"):
        ed = g["event_date"].iloc[0]
        # event trading day
        idx0 = cal.get_indexer([ed], method="bfill")[0]
        est_lo = idx0 + meta["est_start"]
        est_hi = idx0 + meta["est_end"]
        assert est_hi < idx0, "estimation must end strictly before event day"
        assert cal[est_hi] < cal[idx0]


def test_car_identity_sum_of_ars():
    """CAR[-1,1] ≈ AR(-1)+AR(0)+AR(1) within float tolerance."""
    events, returns, market = _synth_panel(30, 350, event_day_index=200, jump=0.015, noise=0.004)
    ar_panel, meta = compute_ar(events, returns, market, model="market", pre=10, post=10)
    lo, hi = PRIMARY_WINDOW
    for eid, g in ar_panel.groupby("event_id"):
        sub = g[(g["tau"] >= lo) & (g["tau"] <= hi)].sort_values("tau")
        if sub["ar"].isna().any() or len(sub) < 3:
            continue
        car = float(sub["ar"].sum())
        manual = float(sub.loc[sub["tau"] == -1, "ar"].iloc[0]
                       + sub.loc[sub["tau"] == 0, "ar"].iloc[0]
                       + sub.loc[sub["tau"] == 1, "ar"].iloc[0])
        assert abs(car - manual) < 1e-12, f"CAR identity fail: {car} vs {manual}"
    cars = car_stats(ar_panel, [PRIMARY_WINDOW])
    assert not cars.empty and cars.iloc[0]["n"] >= 20


def test_html_self_contained():
    """HTML has inline SVG, no external network deps."""
    events, returns, market = _synth_panel(40, 350, event_day_index=200, jump=0.03, noise=0.005)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        rep, _, _ = run_study(e, r, m, model="market")
    html = render_html(rep)
    assert "<svg" in html
    assert "mean CAR" in html or "CAR" in html
    for bad in ('src="http', 'href="http', "<link", "<script src",
                "cdn.", "googleapis", "@import", "url(http"):
        assert bad not in html, f"external resource: {bad}"
    assert "不构成任何投资建议" in html or "research" in html.lower()


def test_json_keys_present():
    """JSON report exposes required keys."""
    events, returns, market = _synth_panel(40, 350, event_day_index=200, jump=0.025, noise=0.005)
    with tempfile.TemporaryDirectory() as td:
        e, r, m = _write_csvs(Path(td), events, returns, market)
        rep, _, _ = run_study(e, r, m, model="market")
    raw = render_json(rep)
    data = json.loads(raw)
    required = [
        "title", "model", "n_input", "n_used", "n_dropped",
        "primary", "car_windows", "ar_path", "caveats", "low_sample_warning",
    ]
    for k in required:
        assert k in data, f"missing key: {k}"
    for pk in ("mean_car", "t_stat", "p_value", "win_rate", "significant_05"):
        assert pk in data["primary"], f"missing primary.{pk}"
    assert isinstance(data["car_windows"], list) and len(data["car_windows"]) >= 1
    assert isinstance(data["ar_path"], list) and len(data["ar_path"]) >= 1


def test_dropped_missing_events_counted():
    """Events with >30% missing AR in window are dropped and counted."""
    n = 45
    events, returns, market = _synth_panel(
        n, 350, event_day_index=200, jump=0.02, noise=0.005,
        miss_symbols={f"S{i:03d}.SH" for i in range(8)},
        miss_frac_in_window=0.5,  # 50% > 30% threshold
    )
    ar_panel, meta = compute_ar(events, returns, market, model="market", pre=10, post=10)
    assert meta["n_dropped_missing"] >= 8, (
        f"expected >=8 missing drops, got {meta['n_dropped_missing']}"
    )
    assert meta["n_used"] == n - meta["n_dropped"]
    assert meta["n_dropped"] >= meta["n_dropped_missing"]
    # Still enough to build report
    cars = car_stats(ar_panel)
    path = average_ar_path(ar_panel)
    rep = build_report(ar_panel, meta, cars, path)
    assert rep["n_dropped_missing"] >= 8
    text = render_text(rep)
    assert "N_dropped" in text or "n_dropped" in text.lower() or "N_used" in text


TESTS = [
    test_planted_positive_jump,
    test_planted_negative_jump,
    test_no_event_effect_not_significant,
    test_usable_event_count_guard,
    test_estimation_window_excludes_event_day,
    test_car_identity_sum_of_ars,
    test_html_self_contained,
    test_json_keys_present,
    test_dropped_missing_events_counted,
]


def main():
    passed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(TESTS)} 通过")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
