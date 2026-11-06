# 给 Codex 的本地计算输入约定

客户不用填写本文件。先保存原始请求/响应，再显式适配真实字段。`null` 是未知，只有来源确实返回或可靠导出证明的零才写 `0`。本包只提供字段约定，不附带测试数据；所有输入由当前用户的真实响应整理。

## 顶层与来源

| 字段 | 要求 |
|---|---|
| schema_version | 数字 1 |
| title | 客户报告标题 |
| expected | 以 ga4、gsc 为键的已核实目标，不能照抄待审输入冒充独立核验 |
| evidence | 证据 ID → `{file: 输入目录内相对路径, sha256: 原文件摘要}` |
| sources | 各来源 current / previous；每个来源至少有 current；缺失来源不伪造 |
| crm | 没有真实销售证据时 `{status: "missing"}`，不借事件数填销售结果 |
| actions | 0–3 项优先行动；空数组只算计算骨架，不算业务分析完成 |

expected 和对应 source 都有 `identity`、`scope`、`timezone`、`currency`、`date_basis`，五项必须一致。

- GA4 identity 是属性 ID；scope 必有生产 `hostname`，保存实际渠道等过滤。timezone 是属性 IANA 时区；currency 取实际设置；date_basis 为 `property_date`。
- GSC identity 是完整 siteUrl；scope 必有 `type` 和 `aggregation_type`。timezone 用 `America/Los_Angeles`；currency 可 null；date_basis 为 `pacific_date`。

每个 source 包含：

1. `id, system, role`；`period: {start: YYYY-MM-DD, end: YYYY-MM-DD}`。两期等长不重叠、前期早于本期；星期结构不同会提示。
2. `evidence_refs` 原始证据 ID 列表；`metadata` 保存抽样、阈值、其他行、截断、GSC `firstIncompleteDate` 等原始信息。
3. `finality: settled / provisional / unknown`。不能因为日期旧就声明成熟；平台处理状态与销售结果成熟度分开。
4. `total` 独立无分组总计；GA4 至少 sessions / totalUsers；GSC 至少 clicks / impressions / ctr / position。不能从明细相加替代。
5. 可选 `reports`：每个有 `name, dimensions, scope, complete, evidence_refs, rows`，可加来源实际 `provider_row_count`。complete 只表示该查询返回的行已取完，不保证平台披露全部需求。

每行是 `{key: [原维度字符串...], metrics: {指标: 数字或null}}`，键唯一、列数一致，无合计行。两期比较对象的维度和 scope 一致；缺一侧行保留未知，不补零。

## 指标

| 来源 | 支持字段 |
|---|---|
| GA4 | sessions、totalUsers、engagedSessions、keyEvents、sessionKeyEventRate、engagementRate、screenPageViews、eventCount、bounceRate、averageSessionDuration |
| GSC | clicks、impressions、ctr、position |

比率输入为 0–1，时长为秒。事件次数不是人数；有关键事件的访问比例直接使用 sessionKeyEventRate。计数不能为小数或负值；遇真实调整按有说明的独立方法处理，不能清零。

report.scope 可以是首页、自然搜索等子范围，不替代来源 total。另一范围独立总计可作为有独立 evidence_refs 的单行报告，明确标签；不能把全站与子范围相加。完整 URL 保留，按路径汇总用户或比率时优先原生查询。

计算器保留可选 Ads 支持以兼容已有辅助证据，本 Skill 不要求它。需要时只使用真实 cost_micros、clicks、impressions、platform_conversions、all_conversions、platform_value，币种/时区与 `interaction_date` 明确。未核对跨期转化目标时保留 conversion_comparability unknown；固定价值不作为收入。无完整同队列销售证据，不启用收益计算。

## 行动与交付

每项必填 `title / fact / hypothesis / action / owner / verification / stop_rule / source_ids`。source_ids 引用来源 ID，引用存在不证明文字结论正确。

- fact：客户能看懂的观察事实与样本。
- hypothesis：可能原因、经营影响以及还不知道什么。
- action：针对具体页面/流程的步骤与预期帮助，不写保证提升。
- owner / verification / stop_rule：谁做、何时看什么结果、什么情况下先停下。

按 [报告规则](reporting.md) 补齐说明。程序只校验结构和计算，不能替代页面实看或销售确认。

输出目录必须不存在，保留历史。生成 report.html、review.json、actions.csv。详细差异与核验 JSON 留客户私有目录；最终 HTML 需要 Codex 补充具体页面、查询和解释，不能把骨架冒充完整分析。
