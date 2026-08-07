# Source Boundary

本 skill 只对用户提供的事件清单与历史收益做 CAR 事件研究统计，不预测、不喊单、不实时告警。

## Allowed sources（允许）

- 用户自备事件 CSV（财报日、公告日、任意公司事件日）
- 用户自备或经合规数据 skill（如 `skill-pandadata-api`）导出的日收益 / 收盘价
- 用户提供的市场指数或基准收益；或文档化的**截面等权市场代理**
- 公开方法论：市场调整 / 均值调整事件研究常识（见 `event-study-methods.md`）

## Not allowed unless the user has rights and explicitly provides them

- 付费墙内一致预期 / 盈利 surprise 专有库（除非用户自备并授权）
- 非公开的内部订单流、未公开业绩草稿
- 把本 skill 输出改写成买卖指令或目标价

## 输出边界

- 只输出：AR/CAR 统计、检验、样本量、模型与窗口披露、自包含图表报告
- 不输出：买卖点、仓位、强制「利好/利空交易」结论
- 每个结论须可追溯：N_used、模型、市场代理、窗口、p 值
- 必须声明：仅供研究参考，不构成投资建议；重叠事件可能高估显著性
