# Portable Loader Prompt

Use this prompt in agents that do not natively discover `SKILL.md` folders.

```text
You have access to a local skill named earnings-event-study at:
<EARNINGS_EVENT_STUDY_SKILL_ROOT>

When the user request matches this skill's SKILL.md description
(财报事件研究 / CAR / 异常收益 / 事件窗口 / earnings event study):
1. Read <EARNINGS_EVENT_STUDY_SKILL_ROOT>/SKILL.md.
2. Follow the workflow and guardrails in that file exactly.
3. Load referenced files under <EARNINGS_EVENT_STUDY_SKILL_ROOT>/references/ only when needed.
4. Run or reason with scripts/event_study.py; do not invent CAR formulas that contradict the skill.
5. Disclose N_used, model choice, market proxy, and windows; refuse if usable events < 20.
6. Do not output trading advice; distinguish this skill from real-time event-risk alerts.
```
