# 方法与参考资料

本 Skill 的说明与本地校验代码为独立编写。以下资料用于核对数据含义和分析方法；不要求客户额外安装其他 Skill。

## 官方依据

- [GA4 Data API 字段说明](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)：核对可用维度、指标及统计范围。
- [GA4 互动率与跳出率](https://support.google.com/analytics/answer/12195621)：解释访问参与情况及其限制。
- [Google 官方 Analytics MCP](https://developers.google.com/analytics/devguides/MCP)：已有 GA4 连接的官方参考。
- [GSC Search Analytics 查询](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)：核对日期、搜索类型、数据状态、聚合和返回限制。
- [GSC 查询与分页](https://developers.google.com/webmaster-tools/v1/how-tos/search_analytics)：按需要读取明细并保留覆盖限制。
- [Codex Skill 使用与项目位置](https://learn.chatgpt.com/docs/build-skills)：本包使用项目 `.agents/skills` 目录，不做全局安装。

## 工作流参考

- [Open Analytics](https://github.com/kostja94/open-analytics/tree/5c5b1d8db4cfad078733e3bbabbb9573c7d5830a)：查询、页面、周期对比与行动延续。
- [Corey Marketing Skills](https://github.com/coreyhaines31/marketingskills/tree/88c79f6528a9347420e57ceb1fff9a60de651768/skills)：业务背景、测量诊断与销售反馈。
- [Google Analytics API Skills](https://github.com/google/skills/tree/main/skills/analytics)：开发取数时按需核对接口用法。

引用不代表官方认证分析结论。使用会变化的 API、工具字段或功能前，以当前接口和官方文档核验；不把工具安装成功或流量增长当作已获得更多合格询盘。
