"""A-share / equity earnings (corporate) event-study: CAR statistics.

Framework-neutral. Inputs are CSVs of events + returns (+ optional market).
Computes market-adjusted or mean-adjusted abnormal returns, CARs over standard
windows, cross-sectional t-tests and binomial sign tests.

No trading advice — disclose N, model, windows, and overlapping-event caveats.

Usage:
    python scripts/event_study.py --events e.csv --returns r.csv \\
        [--market m.csv] --model market --out report/ [--pre 10 --post 10] [--no-html]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_EVENTS_HARD = 20
MIN_EVENTS_WARN = 50
MAX_MISSING_FRAC = 0.30
DEFAULT_PRE = 10
DEFAULT_POST = 10
DEFAULT_EST_START = -120
DEFAULT_EST_END = -21
DEFAULT_CAR_WINDOWS = [(-1, 1), (-1, 0), (0, 1), (-5, 5), (-10, 10)]
PRIMARY_WINDOW = (-1, 1)


# ---------------------------------------------------------------------------
# Stats helpers (no scipy)
# ---------------------------------------------------------------------------

def _binom_two_sided_p(k: int, n: int, p: float = 0.5) -> float:
    """Two-sided exact binomial test that success probability equals p."""
    from math import comb

    if n == 0:
        return float("nan")
    probs = [comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(n + 1)]
    obs = probs[k]
    return float(min(1.0, sum(pr for pr in probs if pr <= obs + 1e-12)))


def _student_t_sf_two_sided(t: float, df: float) -> float:
    """P(|T| > |t|) for T ~ t(df) via regularized incomplete beta."""
    from math import lgamma, log, exp

    df = float(df)
    x = df / (df + t * t)

    def _betacf(a, b, x_):
        fpmin, eps, maxit = 1e-300, 3e-14, 300
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c = 1.0
        d = 1.0 - qab * x_ / qap
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        h = d
        for m in range(1, maxit):
            m2 = 2 * m
            aa = m * (b - m) * x_ / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > fpmin else fpmin)
            c = 1.0 + aa / c
            c = c if abs(c) > fpmin else fpmin
            h *= d * c
            aa = -(a + m) * (qab + m) * x_ / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > fpmin else fpmin)
            c = 1.0 + aa / c
            c = c if abs(c) > fpmin else fpmin
            de = d * c
            h *= de
            if abs(de - 1.0) < eps:
                break
        return h

    def _betai(a, b, x_):
        if x_ <= 0.0:
            return 0.0
        if x_ >= 1.0:
            return 1.0
        lbeta = lgamma(a) + lgamma(b) - lgamma(a + b)
        bt = exp(a * log(x_) + b * log(1.0 - x_) - lbeta)
        if x_ < (a + 1.0) / (a + b + 2.0):
            return bt * _betacf(a, b, x_) / a
        return 1.0 - bt * _betacf(b, a, 1.0 - x_) / b

    return _betai(df / 2.0, 0.5, x)


def _t_test_mean(x) -> tuple[float, float, float]:
    """One-sample t-test H0: mean=0. Returns (mean, t-stat, two-sided p)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    mean = float(np.mean(x))
    sd = float(np.std(x, ddof=1))
    if sd == 0:
        return mean, float("inf") if mean != 0 else 0.0, 0.0 if mean != 0 else 1.0
    t = mean / (sd / math.sqrt(n))
    p = float(min(1.0, _student_t_sf_two_sided(t, n - 1)))
    return mean, float(t), p


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _norm_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize()


def load_events(path: str | Path) -> pd.DataFrame:
    """Load events CSV: symbol, event_date [, event_type]."""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "symbol" not in cols or "event_date" not in cols:
        raise ValueError("events CSV must have columns: symbol, event_date")
    out = pd.DataFrame({
        "symbol": df[cols["symbol"]].astype(str).str.strip(),
        "event_date": _norm_dates(df[cols["event_date"]]),
    })
    if "event_type" in cols:
        out["event_type"] = df[cols["event_type"]].astype(str)
    else:
        out["event_type"] = "earnings"
    out = out.dropna(subset=["symbol", "event_date"]).reset_index(drop=True)
    if out.empty:
        raise ValueError("events CSV is empty after cleaning")
    return out


def load_returns(path: str | Path) -> pd.DataFrame:
    """Load returns CSV: date, symbol, ret  OR  date, symbol, close."""
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols or "symbol" not in cols:
        raise ValueError("returns CSV must have columns: date, symbol, and ret or close")
    out = pd.DataFrame({
        "date": _norm_dates(df[cols["date"]]),
        "symbol": df[cols["symbol"]].astype(str).str.strip(),
    })
    if "ret" in cols:
        out["ret"] = pd.to_numeric(df[cols["ret"]], errors="coerce")
    elif "close" in cols:
        out["close"] = pd.to_numeric(df[cols["close"]], errors="coerce")
        out = out.sort_values(["symbol", "date"])
        out["ret"] = out.groupby("symbol")["close"].pct_change()
        out = out.drop(columns=["close"])
    else:
        raise ValueError("returns CSV needs 'ret' or 'close'")
    out = out.dropna(subset=["date", "symbol", "ret"]).reset_index(drop=True)
    if out.empty:
        raise ValueError("returns CSV is empty after cleaning")
    return out


def load_market(path: str | Path | None, returns: pd.DataFrame) -> pd.DataFrame:
    """Load market returns, or build equal-weight cross-section proxy.

    If ``path`` is None, market return on each date is the equal-weight mean
    of all symbols present in the returns panel that day (documented proxy).
    """
    if path is None:
        mkt = (
            returns.groupby("date", as_index=False)["ret"]
            .mean()
            .rename(columns={"ret": "mkt_ret"})
        )
        mkt.attrs["source"] = "equal_weight_cross_section"
        return mkt

    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    if "date" not in cols:
        raise ValueError("market CSV must have column: date")
    out = pd.DataFrame({"date": _norm_dates(df[cols["date"]])})
    if "mkt_ret" in cols:
        out["mkt_ret"] = pd.to_numeric(df[cols["mkt_ret"]], errors="coerce")
    elif "ret" in cols:
        out["mkt_ret"] = pd.to_numeric(df[cols["ret"]], errors="coerce")
    elif "close" in cols:
        out["close"] = pd.to_numeric(df[cols["close"]], errors="coerce")
        out = out.sort_values("date")
        out["mkt_ret"] = out["close"].pct_change()
        out = out.drop(columns=["close"])
    else:
        raise ValueError("market CSV needs mkt_ret, ret, or close")
    out = out.dropna(subset=["date", "mkt_ret"]).reset_index(drop=True)
    out.attrs["source"] = "file"
    if out.empty:
        raise ValueError("market CSV is empty after cleaning")
    return out


# ---------------------------------------------------------------------------
# Core event study
# ---------------------------------------------------------------------------

def _trading_calendar(returns: pd.DataFrame, market: pd.DataFrame) -> pd.DatetimeIndex:
    """Union of trading dates that appear in returns or market, sorted."""
    dates = pd.Index(returns["date"].unique()).union(pd.Index(market["date"].unique()))
    return pd.DatetimeIndex(sorted(dates))


def _event_day_index(calendar: pd.DatetimeIndex, event_date: pd.Timestamp) -> int | None:
    """Map event calendar date to nearest trading day index (same day or next)."""
    # Exact match first
    loc = calendar.get_indexer([event_date], method=None)[0]
    if loc >= 0:
        return int(loc)
    # Next trading day on or after event_date
    loc = calendar.get_indexer([event_date], method="bfill")[0]
    if loc >= 0:
        return int(loc)
    return None


def compute_ar(
    events: pd.DataFrame,
    returns: pd.DataFrame,
    market: pd.DataFrame,
    model: str = "market",
    pre: int = DEFAULT_PRE,
    post: int = DEFAULT_POST,
    est_start: int = DEFAULT_EST_START,
    est_end: int = DEFAULT_EST_END,
) -> tuple[pd.DataFrame, dict]:
    """Compute abnormal returns for each event over [−pre, +post].

    Returns
    -------
    ar_panel : DataFrame with columns
        event_id, symbol, event_date, tau, date, ret, mkt_ret, ar
    meta : drop / usage counters
    """
    if model not in ("market", "mean"):
        raise ValueError("model must be 'market' or 'mean'")

    calendar = _trading_calendar(returns, market)
    ret_lookup = {
        (row.symbol, row.date): row.ret
        for row in returns.itertuples(index=False)
    }
    mkt_lookup = {row.date: row.mkt_ret for row in market.itertuples(index=False)}

    # Per-symbol return series for mean-adjusted model
    sym_rets: dict[str, pd.Series] = {}
    if model == "mean":
        for sym, g in returns.groupby("symbol"):
            s = g.set_index("date")["ret"].sort_index()
            sym_rets[sym] = s

    rows = []
    n_input = len(events)
    n_dropped_calendar = 0
    n_dropped_missing = 0
    n_dropped_estimation = 0
    drop_reasons: list[dict] = []

    for eid, ev in events.reset_index(drop=True).iterrows():
        sym = ev["symbol"]
        ed = ev["event_date"]
        idx0 = _event_day_index(calendar, ed)
        if idx0 is None:
            n_dropped_calendar += 1
            drop_reasons.append({"symbol": sym, "event_date": str(ed.date()), "reason": "no_calendar"})
            continue

        # Need estimation window entirely before event (no lookahead)
        if model == "mean":
            i_est_lo = idx0 + est_start
            i_est_hi = idx0 + est_end
            if i_est_lo < 0 or i_est_hi >= idx0:
                n_dropped_estimation += 1
                drop_reasons.append({
                    "symbol": sym, "event_date": str(ed.date()),
                    "reason": "estimation_window_invalid",
                })
                continue
            est_dates = calendar[i_est_lo: i_est_hi + 1]
            series = sym_rets.get(sym)
            if series is None:
                n_dropped_estimation += 1
                drop_reasons.append({
                    "symbol": sym, "event_date": str(ed.date()),
                    "reason": "no_symbol_returns",
                })
                continue
            est_vals = series.reindex(est_dates).dropna()
            if len(est_vals) < max(20, int(0.5 * (est_end - est_start + 1))):
                n_dropped_estimation += 1
                drop_reasons.append({
                    "symbol": sym, "event_date": str(ed.date()),
                    "reason": "sparse_estimation",
                })
                continue
            mu = float(est_vals.mean())
            # Guard: estimation must not include event day
            if calendar[idx0] in est_vals.index:
                n_dropped_estimation += 1
                drop_reasons.append({
                    "symbol": sym, "event_date": str(ed.date()),
                    "reason": "estimation_includes_event",
                })
                continue
        else:
            mu = None

        i_lo = idx0 - pre
        i_hi = idx0 + post
        if i_lo < 0 or i_hi >= len(calendar):
            n_dropped_calendar += 1
            drop_reasons.append({
                "symbol": sym, "event_date": str(ed.date()),
                "reason": "window_out_of_range",
            })
            continue

        event_rows = []
        missing = 0
        for tau in range(-pre, post + 1):
            d = calendar[idx0 + tau]
            r = ret_lookup.get((sym, d), np.nan)
            m = mkt_lookup.get(d, np.nan)
            if not np.isfinite(r) or (model == "market" and not np.isfinite(m)):
                ar = np.nan
                missing += 1
            else:
                if model == "market":
                    ar = float(r) - float(m)
                else:
                    ar = float(r) - mu
            event_rows.append({
                "event_id": eid,
                "symbol": sym,
                "event_date": ed,
                "event_type": ev.get("event_type", "earnings"),
                "tau": tau,
                "date": d,
                "ret": float(r) if np.isfinite(r) else np.nan,
                "mkt_ret": float(m) if np.isfinite(m) else np.nan,
                "ar": ar,
                "est_mean": mu if mu is not None else np.nan,
            })

        n_days = pre + post + 1
        if missing / n_days > MAX_MISSING_FRAC:
            n_dropped_missing += 1
            drop_reasons.append({
                "symbol": sym, "event_date": str(ed.date()),
                "reason": "too_many_missing_ar",
                "missing_frac": round(missing / n_days, 3),
            })
            continue

        rows.extend(event_rows)

    ar_panel = pd.DataFrame(rows)
    n_used = int(ar_panel["event_id"].nunique()) if not ar_panel.empty else 0
    meta = {
        "n_input": n_input,
        "n_used": n_used,
        "n_dropped": n_input - n_used,
        "n_dropped_calendar": n_dropped_calendar,
        "n_dropped_missing": n_dropped_missing,
        "n_dropped_estimation": n_dropped_estimation,
        "drop_reasons": drop_reasons,
        "model": model,
        "pre": pre,
        "post": post,
        "est_start": est_start,
        "est_end": est_end,
        "market_source": market.attrs.get("source", "unknown"),
    }
    return ar_panel, meta


def _car_for_event(g: pd.DataFrame, lo: int, hi: int) -> float:
    sub = g[(g["tau"] >= lo) & (g["tau"] <= hi)]
    if sub.empty or sub["ar"].isna().any():
        # allow partial if within missing budget already enforced at event level;
        # for CAR identity tests we need nanmean only when all required days present
        vals = sub["ar"].values
        if len(vals) < (hi - lo + 1) or np.isnan(vals).any():
            # sum of available ARs if any missing inside CAR window → still sum finite
            if np.isfinite(vals).sum() == 0:
                return float("nan")
            if np.isnan(vals).any():
                return float("nan")
        return float(np.nansum(vals))
    return float(sub["ar"].sum())


def car_stats(
    ar_panel: pd.DataFrame,
    windows: list[tuple[int, int]] | None = None,
) -> pd.DataFrame:
    """Cross-sectional CAR statistics for each window."""
    if windows is None:
        windows = list(DEFAULT_CAR_WINDOWS)
    if ar_panel.empty:
        return pd.DataFrame()

    rows = []
    for lo, hi in windows:
        cars = []
        for _, g in ar_panel.groupby("event_id"):
            cars.append(_car_for_event(g, lo, hi))
        cars = np.asarray(cars, dtype=float)
        cars = cars[np.isfinite(cars)]
        n = len(cars)
        mean, t_stat, p_mean = _t_test_mean(cars)
        k_pos = int((cars > 0).sum()) if n else 0
        win_rate = k_pos / n if n else float("nan")
        p_sign = _binom_two_sided_p(k_pos, n) if n else float("nan")
        rows.append({
            "window": f"[{lo},{hi}]",
            "lo": lo,
            "hi": hi,
            "n": n,
            "mean_car": mean,
            "median_car": float(np.median(cars)) if n else float("nan"),
            "std_car": float(np.std(cars, ddof=1)) if n > 1 else float("nan"),
            "t_stat": t_stat,
            "p_value": p_mean,
            "win_rate": win_rate,
            "n_positive": k_pos,
            "p_sign": p_sign,
            "significant_05": bool(np.isfinite(p_mean) and p_mean < 0.05),
        })
    return pd.DataFrame(rows)


def average_ar_path(ar_panel: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional mean AR and 95% CI band by event-time tau."""
    if ar_panel.empty:
        return pd.DataFrame(columns=["tau", "mean_ar", "se", "ci_lo", "ci_hi", "n"])
    rows = []
    for tau, g in ar_panel.groupby("tau"):
        x = g["ar"].dropna().values
        n = len(x)
        mean = float(np.mean(x)) if n else float("nan")
        se = float(np.std(x, ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
        # approx normal CI for display (large N); labeled as such in report
        z = 1.96
        rows.append({
            "tau": int(tau),
            "mean_ar": mean,
            "se": se,
            "ci_lo": mean - z * se if np.isfinite(se) else float("nan"),
            "ci_hi": mean + z * se if np.isfinite(se) else float("nan"),
            "n": n,
        })
    return pd.DataFrame(rows).sort_values("tau").reset_index(drop=True)


def build_report(
    ar_panel: pd.DataFrame,
    meta: dict,
    car_table: pd.DataFrame,
    path_table: pd.DataFrame | None = None,
) -> dict:
    """Assemble JSON-serializable report dict."""
    if meta["n_used"] < MIN_EVENTS_HARD:
        raise ValueError(
            f"Usable events N={meta['n_used']} < {MIN_EVENTS_HARD}; "
            "refuse to report (insufficient sample)."
        )

    low_sample_warning = meta["n_used"] < MIN_EVENTS_WARN
    if path_table is None:
        path_table = average_ar_path(ar_panel)

    primary = car_table[car_table["window"] == f"[{PRIMARY_WINDOW[0]},{PRIMARY_WINDOW[1]}]"]
    primary_row = primary.iloc[0].to_dict() if not primary.empty else {}

    caveats = [
        "本报告为历史事件研究统计，仅供研究参考，不构成任何投资建议。",
        f"异常收益模型：{'市场调整 (AR=ret-mkt)' if meta['model'] == 'market' else '均值调整 (AR=ret-mu_est)'}。",
        f"事件窗口 [-{meta['pre']}, +{meta['post']}]；估计窗 [{meta['est_start']}, {meta['est_end']}]（不含事件日，无前视）。",
        f"市场代理：{'截面等权均值' if meta.get('market_source') == 'equal_weight_cross_section' else '用户提供的市场序列'}。",
        "重叠事件（同一股票短间隔多次财报/公告）会使截面观测非独立，t 检验可能高估显著性。",
        "缺失 AR 超过窗口 30% 的事件已剔除；请核对 N_used / N_dropped。",
    ]
    if low_sample_warning:
        caveats.insert(0, f"警告：可用事件数 N={meta['n_used']} < {MIN_EVENTS_WARN}，统计功效有限，结论须谨慎。")

    return {
        "title": "Earnings / Corporate Event Study (CAR)",
        "model": meta["model"],
        "market_source": meta.get("market_source"),
        "pre": meta["pre"],
        "post": meta["post"],
        "est_start": meta["est_start"],
        "est_end": meta["est_end"],
        "n_input": meta["n_input"],
        "n_used": meta["n_used"],
        "n_dropped": meta["n_dropped"],
        "n_dropped_calendar": meta["n_dropped_calendar"],
        "n_dropped_missing": meta["n_dropped_missing"],
        "n_dropped_estimation": meta["n_dropped_estimation"],
        "low_sample_warning": low_sample_warning,
        "primary_window": f"[{PRIMARY_WINDOW[0]},{PRIMARY_WINDOW[1]}]",
        "primary": {
            "mean_car": primary_row.get("mean_car"),
            "median_car": primary_row.get("median_car"),
            "t_stat": primary_row.get("t_stat"),
            "p_value": primary_row.get("p_value"),
            "win_rate": primary_row.get("win_rate"),
            "p_sign": primary_row.get("p_sign"),
            "significant_05": primary_row.get("significant_05"),
            "n": primary_row.get("n"),
        },
        "car_windows": car_table.to_dict(orient="records"),
        "ar_path": path_table.to_dict(orient="records"),
        "drop_reasons_sample": meta.get("drop_reasons", [])[:20],
        "caveats": caveats,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _html_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_pct(x, digits=2):
    if x is None or not np.isfinite(x):
        return "-"
    return f"{100 * x:.{digits}f}%"


def _fmt_num(x, digits=4):
    if x is None or not np.isfinite(x):
        return "-"
    return f"{x:.{digits}f}"


def render_text(rep: dict) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append(rep["title"])
    lines.append("=" * 64)
    lines.append(f"模型: {rep['model']}  |  市场: {rep.get('market_source')}")
    lines.append(
        f"事件窗 [-{rep['pre']}, +{rep['post']}]  |  "
        f"估计窗 [{rep['est_start']}, {rep['est_end']}]"
    )
    lines.append(
        f"N_input={rep['n_input']}  N_used={rep['n_used']}  "
        f"N_dropped={rep['n_dropped']} "
        f"(calendar={rep['n_dropped_calendar']}, "
        f"missing={rep['n_dropped_missing']}, "
        f"estimation={rep['n_dropped_estimation']})"
    )
    if rep["low_sample_warning"]:
        lines.append(f"*** 警告：可用事件 < {MIN_EVENTS_WARN} ***")
    lines.append("")
    lines.append(f"主窗口 {rep['primary_window']}")
    p = rep["primary"]
    lines.append(
        f"  mean CAR={_fmt_pct(p.get('mean_car'))}  "
        f"median={_fmt_pct(p.get('median_car'))}  "
        f"win_rate={_fmt_pct(p.get('win_rate'), 1)}  "
        f"t={_fmt_num(p.get('t_stat'), 3)}  "
        f"p={_fmt_num(p.get('p_value'), 4)}  "
        f"p_sign={_fmt_num(p.get('p_sign'), 4)}  "
        f"sig@0.05={'YES' if p.get('significant_05') else 'no'}"
    )
    lines.append("")
    lines.append(f"{'window':<12}{'N':>5}{'meanCAR':>10}{'median':>10}"
                 f"{'win%':>8}{'t':>8}{'p':>8}{'p_sign':>8}{'sig':>5}")
    lines.append("-" * 72)
    for w in rep["car_windows"]:
        lines.append(
            f"{w['window']:<12}{w['n']:>5}"
            f"{_fmt_pct(w['mean_car']):>10}"
            f"{_fmt_pct(w['median_car']):>10}"
            f"{_fmt_pct(w['win_rate'], 1):>8}"
            f"{_fmt_num(w['t_stat'], 2):>8}"
            f"{_fmt_num(w['p_value'], 3):>8}"
            f"{_fmt_num(w['p_sign'], 3):>8}"
            f"{('*' if w['significant_05'] else ''):>5}"
        )
    lines.append("")
    lines.append("注意事项:")
    for c in rep["caveats"]:
        lines.append(f"  - {c}")
    lines.append("")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --ground:#f6f3ec; --surface:#fffdf8; --surface-2:#f0ebe0;
    --ink:#23201a; --ink-2:#6b655a; --ink-3:#9a9284;
    --hair:rgba(35,32,26,.12); --hair-strong:rgba(35,32,26,.26);
    --up:#c0392b; --down:#147d6f; --accent:#a9791f;
    --accent-soft:rgba(169,121,31,.12);
    --shadow:0 1px 2px rgba(35,32,26,.06),0 6px 20px rgba(35,32,26,.05);
    --band:rgba(169,121,31,.18);
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--ground); color:var(--ink);
    font-family:"Iowan Old Style","Palatino Linotype","Songti SC","Noto Serif SC",
      Georgia,"PingFang SC","Microsoft YaHei",serif;
    line-height:1.55; font-variant-numeric:tabular-nums; }
  .wrap { max-width:900px; margin:0 auto; padding:40px 24px 72px; }
  .eyebrow { font-size:11px; letter-spacing:.2em; text-transform:uppercase;
    color:var(--accent); font-weight:600; margin:0 0 8px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  h1 { font-size:clamp(22px,4vw,30px); font-weight:700; margin:0 0 8px;
    letter-spacing:-.01em; }
  .meta { color:var(--ink-2); font-size:14px; margin:0 0 4px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }
  .warn { margin:14px 0 0; padding:10px 14px; border-radius:8px; font-size:13px;
    background:rgba(192,57,43,.08); border:1px solid rgba(192,57,43,.28); color:var(--up);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .callout { margin:22px 0 28px; padding:16px 18px; border-radius:10px;
    background:var(--accent-soft); border:1px solid var(--hair);
    display:flex; flex-wrap:wrap; gap:8px 28px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .callout .lead { width:100%; font-weight:600; color:var(--accent); font-size:13px; }
  .callout .stat { font-size:14px; color:var(--ink-2); }
  .callout .stat b { color:var(--ink); font-size:16px; }
  section { margin-top:36px; }
  h2 { font-size:16px; margin:0 0 6px; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .sec-note { font-size:12.5px; color:var(--ink-3); margin:0 0 10px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  .card { background:var(--surface); border:1px solid var(--hair); border-radius:12px;
    box-shadow:var(--shadow); padding:16px 18px 10px; }
  svg { display:block; width:100%; height:auto; }
  table { width:100%; border-collapse:collapse; font-size:13px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  th, td { padding:8px 6px; text-align:right; border-bottom:1px solid var(--hair); }
  th:first-child, td:first-child { text-align:left; }
  th { color:var(--ink-2); font-weight:600; font-size:12px; }
  td.sig { color:var(--accent); font-weight:700; }
  ul.caveats { font-size:13px; color:var(--ink-2); padding-left:18px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }
  footer { margin-top:40px; font-size:12px; color:var(--ink-3);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">QuantSkills · Event Study</p>
  <h1>__TITLE__</h1>
  <p class="meta">模型 <b>__MODEL__</b> · 市场 <b>__MKT__</b> · 事件窗 <b>[-__PRE__, +__POST__]</b></p>
  <p class="meta">N_input=<b>__NIN__</b> · N_used=<b>__NUSED__</b> · N_dropped=<b>__NDROP__</b>
    (calendar=__NDC__ / missing=__NDM__ / estimation=__NDE__)</p>
  __WARN__
  <div class="callout">
    <div class="lead">主窗口 __PWIN__</div>
    <div class="stat">mean CAR <b>__PMEAN__</b></div>
    <div class="stat">t <b>__PT__</b></div>
    <div class="stat">p <b>__PP__</b></div>
    <div class="stat">胜率 <b>__PWINRATE__</b></div>
    <div class="stat">显著@0.05 <b>__PSIG__</b></div>
  </div>

  <section>
    <h2>平均异常收益路径</h2>
    <p class="sec-note">事件日 τ=0；阴影为截面均值 ±1.96×SE（大样本近似）</p>
    <div class="card">__SVG_PATH__</div>
  </section>

  <section>
    <h2>各窗口 CAR</h2>
    <p class="sec-note">截面 t 检验 + 符号检验；* 表示 p&lt;0.05</p>
    <div class="card">__SVG_BARS__</div>
    <div class="card" style="margin-top:14px; overflow-x:auto;">
      __TABLE__
    </div>
  </section>

  <section>
    <h2>注意事项</h2>
    <ul class="caveats">__CAVEATS__</ul>
  </section>
  <footer>Warm paper report · pandas/numpy only · not investment advice</footer>
</div>
</body>
</html>
"""


def _svg_ar_path(path_rows: list[dict]) -> str:
    if not path_rows:
        return "<svg viewBox='0 0 720 280'><text x='20' y='40'>无数据</text></svg>"
    W, H = 720, 280
    padL, padR, padT, padB = 48, 24, 20, 40
    xs = [r["tau"] for r in path_rows]
    ys = [r["mean_ar"] for r in path_rows]
    ci_lo = [r.get("ci_lo", r["mean_ar"]) for r in path_rows]
    ci_hi = [r.get("ci_hi", r["mean_ar"]) for r in path_rows]
    y_all = [v for v in ys + ci_lo + ci_hi if v is not None and np.isfinite(v)]
    ymin = min(y_all + [0.0])
    ymax = max(y_all + [0.0])
    span = ymax - ymin or 1e-6
    ymin -= 0.08 * span
    ymax += 0.08 * span

    def X(tau):
        return padL + (tau - xs[0]) / (xs[-1] - xs[0] or 1) * (W - padL - padR)

    def Y(v):
        return padT + (ymax - v) / (ymax - ymin) * (H - padT - padB)

    # CI band polygon
    band_pts = [(X(r["tau"]), Y(r["ci_hi"] if np.isfinite(r.get("ci_hi", np.nan)) else r["mean_ar"]))
                for r in path_rows]
    band_pts += [(X(r["tau"]), Y(r["ci_lo"] if np.isfinite(r.get("ci_lo", np.nan)) else r["mean_ar"]))
                 for r in reversed(path_rows)]
    band_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in band_pts) + " Z"

    line_d = "M " + " L ".join(f"{X(r['tau']):.1f},{Y(r['mean_ar']):.1f}" for r in path_rows)
    zero_y = Y(0)
    # event day marker
    x0 = X(0) if 0 in xs else None

    ticks = "".join(
        f'<line x1="{X(t):.1f}" y1="{H-padB}" x2="{X(t):.1f}" y2="{H-padB+4}" '
        f'stroke="#6b655a" stroke-width="1"/>'
        f'<text x="{X(t):.1f}" y="{H-12}" text-anchor="middle" '
        f'font-size="11" fill="#6b655a" font-family="sans-serif">{t}</text>'
        for t in xs if t % 2 == 0 or t in (-1, 0, 1)
    )
    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="平均AR路径">'
        f'<rect width="{W}" height="{H}" fill="#fffdf8"/>'
        f'<line x1="{padL}" y1="{zero_y:.1f}" x2="{W-padR}" y2="{zero_y:.1f}" '
        f'stroke="rgba(35,32,26,.2)" stroke-dasharray="4 3"/>'
        f'<path d="{band_d}" fill="rgba(169,121,31,.18)" stroke="none"/>'
        f'<path d="{line_d}" fill="none" stroke="#a9791f" stroke-width="2.2"/>'
    ]
    if x0 is not None:
        parts.append(
            f'<line x1="{x0:.1f}" y1="{padT}" x2="{x0:.1f}" y2="{H-padB}" '
            f'stroke="rgba(192,57,43,.45)" stroke-width="1.5"/>'
            f'<text x="{x0:.1f}" y="{padT+12}" text-anchor="middle" '
            f'font-size="11" fill="#c0392b" font-family="sans-serif">τ=0</text>'
        )
    for r in path_rows:
        parts.append(
            f'<circle cx="{X(r["tau"]):.1f}" cy="{Y(r["mean_ar"]):.1f}" r="2.5" fill="#a9791f"/>'
        )
    parts.append(ticks)
    parts.append("</svg>")
    return "".join(parts)


def _svg_car_bars(car_rows: list[dict]) -> str:
    if not car_rows:
        return "<svg viewBox='0 0 720 240'><text x='20' y='40'>无数据</text></svg>"
    W, H = 720, 260
    padL, padR, padT, padB = 48, 24, 24, 48
    vals = [r["mean_car"] for r in car_rows]
    ymin = min(vals + [0.0])
    ymax = max(vals + [0.0])
    span = ymax - ymin or 1e-6
    ymin -= 0.12 * span
    ymax += 0.12 * span
    n = len(car_rows)
    gap = 12
    bar_w = (W - padL - padR - gap * (n - 1)) / n
    zero_y = padT + (ymax - 0) / (ymax - ymin) * (H - padT - padB)

    def Y(v):
        return padT + (ymax - v) / (ymax - ymin) * (H - padT - padB)

    parts = [
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="CAR柱状图">'
        f'<rect width="{W}" height="{H}" fill="#fffdf8"/>'
        f'<line x1="{padL}" y1="{zero_y:.1f}" x2="{W-padR}" y2="{zero_y:.1f}" '
        f'stroke="rgba(35,32,26,.25)" stroke-width="1"/>'
    ]
    for i, r in enumerate(car_rows):
        x = padL + i * (bar_w + gap)
        y0 = zero_y
        y1 = Y(r["mean_car"])
        top = min(y0, y1)
        h = abs(y1 - y0)
        color = "#c0392b" if r["mean_car"] >= 0 else "#147d6f"
        opacity = "1" if r.get("significant_05") else "0.35"
        parts.append(
            f'<rect x="{x:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{max(h,1):.1f}" '
            f'fill="{color}" opacity="{opacity}" rx="3"/>'
        )
        parts.append(
            f'<text x="{x + bar_w/2:.1f}" y="{H-28}" text-anchor="middle" '
            f'font-size="11" fill="#6b655a" font-family="sans-serif">{r["window"]}</text>'
        )
        label = _fmt_pct(r["mean_car"], 2)
        parts.append(
            f'<text x="{x + bar_w/2:.1f}" y="{top - 6:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#23201a" font-family="sans-serif">{label}'
            f'{"*" if r.get("significant_05") else ""}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def render_html(rep: dict) -> str:
    p = rep["primary"]
    warn = ""
    if rep["low_sample_warning"]:
        warn = (
            f'<p class="warn">警告：可用事件 N={rep["n_used"]} &lt; {MIN_EVENTS_WARN}，'
            "统计功效有限。</p>"
        )
    table_rows = []
    table_rows.append(
        "<table><thead><tr>"
        "<th>窗口</th><th>N</th><th>mean CAR</th><th>median</th>"
        "<th>胜率</th><th>t</th><th>p</th><th>p_sign</th><th></th>"
        "</tr></thead><tbody>"
    )
    for w in rep["car_windows"]:
        sig = "*" if w["significant_05"] else ""
        cls = ' class="sig"' if w["significant_05"] else ""
        table_rows.append(
            f"<tr{cls}><td>{_html_escape(w['window'])}</td>"
            f"<td>{w['n']}</td>"
            f"<td>{_html_escape(_fmt_pct(w['mean_car']))}</td>"
            f"<td>{_html_escape(_fmt_pct(w['median_car']))}</td>"
            f"<td>{_html_escape(_fmt_pct(w['win_rate'], 1))}</td>"
            f"<td>{_html_escape(_fmt_num(w['t_stat'], 3))}</td>"
            f"<td>{_html_escape(_fmt_num(w['p_value'], 4))}</td>"
            f"<td>{_html_escape(_fmt_num(w['p_sign'], 4))}</td>"
            f"<td>{sig}</td></tr>"
        )
    table_rows.append("</tbody></table>")
    caveats = "".join(f"<li>{_html_escape(c)}</li>" for c in rep["caveats"])

    html = _HTML_TEMPLATE
    repl = {
        "__TITLE__": _html_escape(rep["title"]),
        "__MODEL__": _html_escape(rep["model"]),
        "__MKT__": _html_escape(str(rep.get("market_source"))),
        "__PRE__": str(rep["pre"]),
        "__POST__": str(rep["post"]),
        "__NIN__": str(rep["n_input"]),
        "__NUSED__": str(rep["n_used"]),
        "__NDROP__": str(rep["n_dropped"]),
        "__NDC__": str(rep["n_dropped_calendar"]),
        "__NDM__": str(rep["n_dropped_missing"]),
        "__NDE__": str(rep["n_dropped_estimation"]),
        "__WARN__": warn,
        "__PWIN__": _html_escape(rep["primary_window"]),
        "__PMEAN__": _html_escape(_fmt_pct(p.get("mean_car"))),
        "__PT__": _html_escape(_fmt_num(p.get("t_stat"), 3)),
        "__PP__": _html_escape(_fmt_num(p.get("p_value"), 4)),
        "__PWINRATE__": _html_escape(_fmt_pct(p.get("win_rate"), 1)),
        "__PSIG__": "YES" if p.get("significant_05") else "no",
        "__SVG_PATH__": _svg_ar_path(rep.get("ar_path", [])),
        "__SVG_BARS__": _svg_car_bars(rep.get("car_windows", [])),
        "__TABLE__": "".join(table_rows),
        "__CAVEATS__": caveats,
    }
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def render_json(rep: dict) -> str:
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, (np.floating, float)):
            if not np.isfinite(o):
                return None
            return float(o)
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.bool_, bool)):
            return bool(o)
        return o

    return json.dumps(_clean(rep), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run_study(
    events_path: str,
    returns_path: str,
    market_path: str | None = None,
    model: str = "market",
    pre: int = DEFAULT_PRE,
    post: int = DEFAULT_POST,
    est_start: int = DEFAULT_EST_START,
    est_end: int = DEFAULT_EST_END,
    car_windows: list[tuple[int, int]] | None = None,
) -> tuple[dict, pd.DataFrame, dict]:
    events = load_events(events_path)
    returns = load_returns(returns_path)
    market = load_market(market_path, returns)
    ar_panel, meta = compute_ar(
        events, returns, market,
        model=model, pre=pre, post=post,
        est_start=est_start, est_end=est_end,
    )
    if car_windows is None:
        # Clip default windows to available pre/post
        car_windows = [
            (lo, hi) for lo, hi in DEFAULT_CAR_WINDOWS
            if lo >= -pre and hi <= post
        ]
        if not car_windows:
            car_windows = [(-min(1, pre), min(1, post))]
    cars = car_stats(ar_panel, car_windows)
    path = average_ar_path(ar_panel)
    rep = build_report(ar_panel, meta, cars, path)
    return rep, ar_panel, meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Earnings / corporate event study (CAR)")
    p.add_argument("--events", required=True, help="CSV: symbol,event_date[,event_type]")
    p.add_argument("--returns", required=True, help="CSV: date,symbol,ret|close")
    p.add_argument("--market", default=None, help="optional CSV: date,mkt_ret|close")
    p.add_argument("--model", choices=["market", "mean"], default="market")
    p.add_argument("--pre", type=int, default=DEFAULT_PRE)
    p.add_argument("--post", type=int, default=DEFAULT_POST)
    p.add_argument("--est-start", type=int, default=DEFAULT_EST_START)
    p.add_argument("--est-end", type=int, default=DEFAULT_EST_END)
    p.add_argument("--out", default=None, help="output directory")
    p.add_argument("--no-html", action="store_true")
    args = p.parse_args(argv)

    try:
        rep, _, _ = run_study(
            args.events, args.returns, args.market,
            model=args.model, pre=args.pre, post=args.post,
            est_start=args.est_start, est_end=args.est_end,
        )
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    text = render_text(rep)
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.buffer.write((text + "\n").encode(enc, errors="replace"))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "event_study.txt").write_text(text, encoding="utf-8")
        (out / "event_study.json").write_text(render_json(rep), encoding="utf-8")
        if not args.no_html:
            (out / "event_study.html").write_text(render_html(rep), encoding="utf-8")
        print(f"Wrote report to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
