---
name: earnings-event-study
description: >-
  Formal CAR event-study for A-share / equity earnings (or any corporate) events:
  abnormal returns, cumulative abnormal returns, cross-sectional t-tests and sign tests
  across event windows. Use when the user asks 财报事件研究, CAR, 异常收益, 事件窗口,
  earnings surprise reaction, average abnormal return around earnings, or event-study
  statistics — not for real-time event-risk monitoring/alerts.
license: GPL-3.0-only
metadata:
  organization: QuantSkills
  organization_url: https://github.com/quantskills
  repository: skill-earnings-event-study
  repository_url: https://github.com/quantskills/skill-earnings-event-study
  project_type: skill
  collection: earnings-event-study
quantSkills:
  project_type: skill
  category: analyst
  tags:
    - event-study
    - car
    - earnings
    - abnormal-return
    - a-share
  platforms:
    - claude-code
    - codex
    - cursor
    - hermes
    - openclaw
  language: zh-en
  status: stable
  validation_level: runnable
  maintainer_type: community
  requires: []
  summary_zh: 对财报/公司事件做正式 CAR 事件研究：异常收益、多窗口累计异常收益、截面 t 检验与符号检验；披露样本量与模型，不输出买卖建议。
  summary_en: Formal CAR event study around earnings/corporate events — abnormal returns, multi-window CARs, t-tests and sign tests, with sample and model disclosure; research only, no trading advice.
---

# Earnings Event Study（财报 / 公司事件 CAR 研究）

回答：**围绕财报（或任意公司事件），平均异常收益是多少？是否显著？不同窗口是否稳健？**

与 `event-risk-alert`（监控/告警）不同：本 skill 做的是**正式事件研究统计**（AR / CAR / 检验），不是实时风险推送。

## 何时使用

- 「这批财报前后平均超额收益多少？显著吗？」
- 「earnings surprise 的市场反应 CAR[-1,1] / [-5,5]」
- 「用市场调整或均值调整模型做事件窗口稳健性」

## 何时不用

- 实时公告监控、阈值告警 → 用事件风险/告警类 skill
- 单票基本面尽调、同业对标、因子挖掘 → 用对应 skill
- 需要给出买卖点、仓位或「该不该买」→ **本 skill 明确拒绝**

## 输入

| 文件 | 列 | 说明 |
|------|----|------|
| `--events` | `symbol,event_date`（可选 `event_type`） | 事件清单 |
| `--returns` | `date,symbol,ret` 或 `date,symbol,close` | 个股收益；给 close 则内部算 ret |
| `--market`（可选） | `date,mkt_ret` 或 `date,close` | 市场收益；**缺省则用当日截面等权均值作市场代理**（须在报告中披露） |

## 模型与窗口

- **market**：`AR = ret − mkt_ret`（默认，透明）
- **mean**：`AR = ret − μ̂`，估计窗默认 **[−120, −21]**（止于事件前，**无前视**）
- 事件窗默认 **[−10, +10]**（`--pre` / `--post`）
- CAR 窗口：`[-1,1]`（主窗口）、`[-1,0]`、`[0,1]`、`[-5,5]`、`[-10,10]`（可裁剪到可用窗）

## 工作流

```text
确认事件清单 + 收益面板（+ 可选市场）
  → python scripts/event_study.py --events … --returns … [--market …] --model market|mean --out report/
  → 阅读 N_used / N_dropped、主窗口 mean CAR / t / p / 胜率
  → 对照多窗口稳健性 + AR 路径图
  → 用 references/event-study-methods.md 解释口径与局限
```

### CLI

```bash
python scripts/event_study.py --events e.csv --returns r.csv [--market m.csv] \
  --model market --out report/ [--pre 10 --post 10] [--no-html]
```

输出（`--out`）：`event_study.txt`、`event_study.json`、`event_study.html`（自包含 SVG）。

```json qsh-form
{
  "version": 1,
  "task": {
    "placeholder": "补充事件清单路径、收益面板、研究问题（可选）",
    "required": false
  },
  "fields": [
    {
      "key": "events",
      "label": "事件 CSV",
      "type": "text",
      "placeholder": "examples/data/events.csv（symbol,event_date）"
    },
    {
      "key": "returns",
      "label": "收益/行情 CSV",
      "type": "text",
      "placeholder": "examples/data/returns.csv（date,symbol,ret|close）"
    },
    {
      "key": "market",
      "label": "市场 CSV（可选）",
      "type": "text",
      "placeholder": "缺省则用截面等权市场代理"
    },
    {
      "key": "model",
      "label": "异常收益模型",
      "type": "select",
      "default": "market",
      "options": [
        { "value": "market", "label": "市场调整" },
        { "value": "mean", "label": "均值调整" }
      ]
    },
    {
      "key": "pre",
      "label": "事件前窗口（交易日）",
      "type": "number",
      "default": 10
    },
    {
      "key": "post",
      "label": "事件后窗口（交易日）",
      "type": "number",
      "default": 10
    }
  ],
  "prompt_template": "{{#task}}任务与材料：\n{{task}}\n\n{{/task}}{{#attachments}}用户上传的材料（已放入工作区）：\n{{attachments}}\n\n{{/attachments}}对事件清单 {{events}}、收益面板 {{returns}}{{#market}}、市场 {{market}}{{/market}} 做正式 CAR 事件研究；模型={{model}}，窗口=[−{{pre}}, +{{post}}]。先读 SKILL.md 与 references/event-study-methods.md，再运行 scripts/event_study.py，披露 N_used/N_dropped、主窗口 mean CAR/t/p/胜率与多窗口稳健性；事实优先，不给买卖建议。"
}
```

## 护栏与严谨性

- 可用事件 **N < 20 → 拒绝出报告**；**N < 50 → 警告**
- 事件窗内缺失 AR **> 30%** → 剔除该事件，并计入 `N_dropped_missing`
- 估计窗不得包含事件日（无 lookahead）
- **必须披露**：N_input / N_used / N_dropped、模型、市场代理来源、窗口
- **重叠事件**（同股短间隔多次公告）会使截面观测非独立，t 检验可能高估显著性——须在结论中提示
- 结尾固定：`本报告基于公开数据与规则化分析生成，仅供研究参考，不构成任何投资建议。`

## 自检

```bash
python scripts/validate.py
```

## 资源

- `references/event-study-methods.md` — 方法口径、检验、局限
- `references/source_boundary.md` — 数据与输出边界
- `examples/data/`、`examples/output/` — 演示输入与报告
