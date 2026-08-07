# skill-earnings-event-study

[简体中文](README.md) | **English**

> Formal CAR event study for earnings / corporate events: abnormal returns, multi-window CARs, cross-sectional t-tests and sign tests.

`earnings-event-study` is a QuantSkills-style agent skill. It answers: **around these events, what is the average abnormal return, is it significant, and is it robust across windows?** It is research statistics — not a real-time event-risk alert tool.

## What it solves

- Alert / monitor skills answer “is there something to watch?”
- **This skill** answers “what is mean CAR, t-stat, win rate, and window robustness for this event sample?”

## Dependencies

- Python 3.10+
- `pandas`, `numpy` (`requirements.txt`)
- Prepare CSVs yourself or via a data skill such as `skill-pandadata-api`; this package does not fetch market data online.

## Install

```bash
mkdir -p .cursor/skills
cp -r skill-earnings-event-study .cursor/skills/earnings-event-study
pip install -r requirements.txt
```

## Quick start

```bash
python scripts/event_study.py \
  --events examples/data/events.csv \
  --returns examples/data/returns.csv \
  --market examples/data/market.csv \
  --model market \
  --out examples/output/
```

Validate:

```bash
python scripts/validate.py
```

## Inputs

| File | Columns | Notes |
|------|---------|-------|
| `--events` | `symbol,event_date` [,`event_type`] | Event list |
| `--returns` | `date,symbol,ret` or `close` | Stock returns |
| `--market` (optional) | `date,mkt_ret` or `close` | If omitted, equal-weight cross-section of the returns universe is used as market proxy (always disclose) |

Models: `market` (`AR = ret − mkt`) or `mean` (estimation window default `[−120,−21]`, no lookahead). Guards: refuse if usable N < 20; warn if N < 50; drop events with >30% missing AR in the event window.

## Example prompts

```text
Run a CAR event study on these earnings dates; report mean CAR[-1,1] with t and p
Compare market-adjusted vs mean-adjusted robustness across [-5,5] and [-10,10]
No index series available — use equal-weight market proxy and disclose it
```

## License

GPL-3.0-only
