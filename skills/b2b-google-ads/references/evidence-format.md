# 本地计算输入约定

这是给执行分析的 Codex 的约定，客户不需要填写。它不是平台响应格式。先保存原始请求/响应，再做显式字段映射。输入中 `null` 表示未知，只有平台返回或可靠导出证明的 0 才能写 0。

本包不附带测试数据。按下列字段约定适配用户的真实响应；环境检查可运行 `review.py --help`，不能把环境检查当作已连接账户。

## 顶层

| 字段 | 约定 |
|---|---|
| schema_version | 数字 1 |
| title | 客户报告标题 |
| expected | 以 ads / ga4 / gsc 为键，填已核验目标上下文；不能从待审数据反向照抄来冒充核验 |
| evidence | 证据 ID → `{file: 相对输入目录文件路径, sha256: 原文件摘要}`；包含原始请求和响应，路径不能越出输入目录 |
| sources | 各来源 current / previous；至少一个 current，同来源不重复 |
| crm | 没有真实销售队列时 `{status: "missing"}`；Mock 明确 status mock |
| actions | 0–3 项；0 项只算计算骨架，不是完成的业务诊断 |
| conversion_comparability | Ads 跨期转化口径：`{status: unknown / changed / verified_same}`；后两种还需 reason、evidence_refs。未知或变化时 HTML 不输出转化/价值/平台 CPA 的效率变化；JSON 保留纯算术差值，不能据此下经营结论 |

expected 和每个 source 都包含：`identity`（账户/属性/站点字符串）、`scope`（完整过滤对象）、`timezone`（IANA）、`currency`（Ads 用 ISO 代码，GSC 可 null）、`date_basis`。这五项必须完全一致。GA scope 必有 `hostname`；GSC scope 必有 `type`、`aggregation_type`。

日期依据限 `ads: interaction_date`、`ga4: property_date`、`gsc: pacific_date`。当前脚本不支持把按转化发生日的广告数据当获客成本队列；需要该分析时单独做，不改标签骗过验证。

## source

- id、system、role；period 为 `{start: YYYY-MM-DD, end: YYYY-MM-DD}`。两期等长、前期早于本期且不重叠；星期不同会提示。不同来源可有各自成熟窗口，不强行改成同一时区事件连接。
- evidence_refs：原始证据 ID 列表；metadata：保留平台原始元数据。GSC `firstIncompleteDate` 和 `first_incomplete_date` 均支持；GA 抽样/阈值/其他行/截断提示保留。
- finality：settled / provisional / unknown。只有已核实的数据状态与业务延迟才标 settled，不能因日期久远就假定销售结果成熟。
- total：独立无分组总计，不能从下面行合成。Ads 必有 cost_micros/clicks/impressions；GA 必有 sessions/totalUsers；GSC 必有 clicks/impressions/ctr/position。未知可 null。
- reports：可选有界明细；`name, dimensions, scope, complete, evidence_refs, rows`，以及可选 provider_row_count（来源真实返回数）。complete 的 true 仅表示该请求的行已取完，不代表平台未隐藏任何词。
- 每行 `{key: [原维度字符串...], metrics: {字段: 数字或null}}`。同一报表键唯一、列一致，不含合计行。缺一侧的行保留 presence=false 与差值 unknown，不补零。

指标白名单：

| 来源 | 字段 |
|---|---|
| ads | cost_micros、clicks、impressions、platform_conversions、all_conversions、platform_value |
| ga4 | sessions、totalUsers、engagedSessions、keyEvents、sessionKeyEventRate、engagementRate、screenPageViews、eventCount、bounceRate、averageSessionDuration |
| gsc | clicks、impressions、ctr、position |

Ads conversions → platform_conversions；conversions_value → platform_value，不改名成询盘或收入。金额 micros 一次转换；比率输入为 0–1。该计算器目前仅支持非负平台指标；遇到真实负调整先调查/另作有说明的处理，不能清零或取绝对值。CRM 净收入/毛利允许负值以保留退款/亏损。

`campaigns` 必须一系列 ID 一行，与总计范围相同且 complete=true 时对账各可用加总指标。缺失项不得声称已对账。`search_terms` 先聚合到 campaign_id + 原始 query；更细的日期/设备/广告组数据保留原导出，显式聚合后再送本筛查计算器。GSC 页面键保留完整 URL；不得裁掉参数或尾斜杠。

报告层 scope 可以比全站窄，例如 GA4 google / cpc 落地页或 GSC 首页查询。两期相同比较对象的维度和 scope 必须一致。它们不替代来源 total；程序不对 GSC 页面/查询和 GA4 用户做加总总计校验。

同系统的另一范围独立总计（例如全站、付费或首页）可作为有独立 evidence_refs 的单行报告，明确 scope 和维度标签，不把子范围与全站相加。率的变化在 HTML 用百分点；时长单位秒，事件次数不等于人数。当前来源 total 仍只对应 expected 指定的一个主范围。

## CRM 只在证据齐全时启用

`status: observed` 时需 evidence_refs、binding（与 Ads current 的五项上下文及 period 完全一致）、checks 中 attribution_verified / deduplicated / sales_validated / cohort_mature 全 true；并有整数 valid_inquiries、qualified_inquiries、pending_reviews。pending_reviews 必须 0 才出最终经济性；net_revenue、gross_profit 可 null。不能把“当前月成交”绑定到“当前月点击”而忽略过去获客队列。

检查项要有可查凭据和责任人，不是为了出数字勾 true。程序仅核对声明与数值一致性，不能验证销售判断、追踪真实性或因果。导入成功、文件哈希相符都不足以证明这些前提。

## actions 与输出

每项必填 title / fact / hypothesis / action / owner / verification / stop_rule，以及 source_ids（来源 ID 列表）。文字要与所引用的证据一致；存在引用不代表程序自动校验了中文结论。

输出目录必须不存在，防覆盖历史。产物：report.html、review.json、actions.csv。HTML 只显示有界摘要，详细行差异留本地 JSON。报告中 null 显示“缺失或分母为零”，JSON 通过 basis 分辨缺失和零基线；非可比数据不计算伪增长率。输出需和客户问题一起审阅，不把脚本通过叫经营效果通过。
