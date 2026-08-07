# skill-earnings-event-study

**简体中文** | [English](README.en.md)

> 对财报/公司事件做正式 CAR 事件研究：异常收益、多窗口累计异常收益、截面显著性与符号检验。

`earnings-event-study` 是 QuantSkills 风格的 Agent Skill，回答：**事件前后平均异常收益是多少、是否显著、多窗口是否稳健**。不同于实时事件风险告警，本 skill 输出的是可复现的统计报告。

## 解决什么问题

- 事件监控类 skill 回答「有没有异常公告 / 要不要盯」
- **本 Skill** 回答「这批事件的平均 CAR、t 检验、胜率、窗口稳健性」

## 依赖

- Python 3.10+
- `pandas`、`numpy`（见 `requirements.txt`）
- 数据可由 `skill-pandadata-api` 等取数 skill 准备后导出为 CSV；本仓库脚本本身不联网取数

## 安装

```bash
# Cursor（项目级）
mkdir -p .cursor/skills
cp -r skill-earnings-event-study .cursor/skills/earnings-event-study

# Claude Code / Codex
cp -r skill-earnings-event-study ~/.claude/skills/earnings-event-study

pip install -r requirements.txt
```

## 快速开始

```bash
python scripts/event_study.py \
  --events examples/data/events.csv \
  --returns examples/data/returns.csv \
  --market examples/data/market.csv \
  --model market \
  --out examples/output/
```

自检：

```bash
python scripts/validate.py
```

## 示例提问

```text
帮我对这批财报事件做 CAR 事件研究，主窗口 [-1,1]，看平均异常收益是否显著
用市场调整模型跑 earnings event study，并对比 [-5,5] 与 [-10,10]
没有指数收益时，用截面等权当市场代理可以吗？请披露口径
```

## 目录

```text
skill-earnings-event-study/
├── SKILL.md
├── README.md
├── README.en.md
├── requirements.txt
├── scripts/
│   ├── event_study.py
│   └── validate.py
├── references/
│   ├── event-study-methods.md
│   └── source_boundary.md
├── agents/
│   ├── openai.yaml
│   ├── cursor-rule.mdc
│   └── portable-loader.md
└── examples/
    ├── data/
    └── output/
```

## License

GPL-3.0-only
