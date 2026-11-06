# MCP 取数指引

以运行时接口为准，先查资源 metadata 再组查询。官方 MCP 当前只读；接入成功不需要为分析另申请 Developer Token。[官方指南](https://developers.google.com/google-ads/api/docs/developer-toolkit/mcp-server)

## 已验证的接口形状

2026-11-05 运行时提供：`customers_list_accessible_customers`、`metadata_get_resource_metadata`、`search_search`，服务前缀可变。search 参数是 `customer_id, resource, fields, conditions, orderings, limit, login_customer_id`，并非 `query`。其他版本如明确支持 GAQL 则按其 schema，不把本例硬塞进去。

先核对客户 ID、币种、时区、manager 标志；管理账号只用于发现实际客户账户及授权链，不把经理总览当单一币种账户。listAccessibleCustomers 只列直接可访问账户，不是全部 MCC 后代。明确目标 ID 且可读时不要求用户重复选；需经理授权路径时才传已核实 login_customer_id。

示例（占位符必须换成用户已核实的值；先 metadata）：

```json
{
  "customer_id": "<目标客户ID>",
  "resource": "campaign",
  "fields": ["campaign.id", "campaign.name", "campaign.advertising_channel_type", "metrics.cost_micros", "metrics.clicks", "metrics.impressions", "metrics.conversions", "metrics.conversions_value"],
  "conditions": ["segments.date BETWEEN '<开始日期>' AND '<结束日期>'"],
  "limit": 10000
}
```

此处不按当前系列状态过滤。需要“当前启用”子集时另外定义并解释差额。metadata 验证字段可选不代表任意字段组合可用；API 报错后缩小字段组修正，不能悄悄换成不同定义的指标。

## 任务所需资源

| 用途 | 资源/组合 | 边界 |
|---|---|---|
| 期间总量、时区币种 | customer | 与明细独立核对 |
| 系列 / 设备 / 网络 / 日期 | campaign + 必要 segments | 分段各自读取，不把多种切片相加 |
| 关键词 | keyword_view + criterion ID | 关键词不是搜索字词 |
| Search 字词广告组细分 | search_term_view | 不包含 PMax |
| Search + PMax 字词 | campaign_search_term_view | 系列粒度；不加关键词相关 segments，否则 PMax 被过滤 |
| 字词洞察类别 | customer_search_term_insight / campaign_search_term_insight | 非同一明细，不假设有 cost，不与词表相加 |
| 转化配置 | conversion_action | 独立全量；区分 ENABLED/HIDDEN/REMOVED |
| 动作表现 | campaign 或 customer + segments.conversion_action | 不带 cost；按资源 ID 关联 |
| 目标关系 | customer_conversion_goal、campaign_conversion_goal、conversion_goal_campaign_config、custom_conversion_goal | 跨账户 conversion customer 与权限另核实 |
| 价值修正 | conversion_value_rule、conversion_value_rule_set | 核实规则条件、启用状态、账户/系列挂载；平台报告价值可含修正 |
| 离线上传 | offline_conversion_upload_conversion_action_summary | 空结果不证明没有上传；健康不证明业务质量 |
| 已有否定 | customer_negative_criterion、campaign_criterion、ad_group_criterion、shared_set、shared_criterion、campaign_shared_set | 分别查 negative/status，核实挂载与成员覆盖 |
| 最近变更 | change_event | 最近 30 天内、最多 10,000 行；不带 metrics；超过限制需分片 |
| 地域、落地页等 | 先 metadata 选对应 view | 用户所在地与目标地、最终 URL 与展开 URL 要区分 |

粒度要求：user_location_view 选择 campaign.id 后，键需同时包括系列与该 view 的 resource_name（国家及 targeting_location），不能仅按国家或 resource_name 去重。全量校验后才按国家聚合。landing_page_view 的多个 URL 切片须单独核对，不与设备/国家花费相加。

这些是取数路线，不保证当前工具支持所有字段组合；调用前核对实际工具 schema 与元数据。实时 metadata 不支持的资源标 NOT_AVAILABLE，并解释影响。

## 完整性与输出适配

关键查询可先运行 `evidence_guard.py query <query.json>`。输入使用 `resource / fields / conditions` 并增加 `includes_pmax` 布尔值；该额外字段只给本地校验，不传给 MCP。它阻止已知 PMax 关键词过滤、错误字词视图、动作分段带花费和两个非法日期宏；通过仍需 metadata 与 API 校验，不能宣称通用 GAQL 校验器。

先检查当前接口是否支持分页；没有 page_token 等参数时不得杜撰分页调用。达到 limit 时，用互斥日期/系列切片并保留切片清单，全部未截断后汇总。不同切片含同一目标词时，按系列 ID + 字词重新汇总；不能取一个分片的零转化覆盖另一个分片的转化。

优先读取 `structuredContent.result`；若接口只给文本 JSON，严格解析，保留字段名。工具错误不能包装为成功空列表。取原始回应后映射短字段并按 evidence-contract 校验；任何丢字段、截断或类型变化必须可见。

读数顺序：身份 → 总量 → 主要系列 → 任务必要明细 → 补历史反例 / 配置 → 同口径核对。不要默认拉所有账户的所有明细。报告记实际读取时间；不把历史快照标题换成“最新三天”。

官方校准：[日期](https://developers.google.com/google-ads/api/docs/query/date-ranges) · [PMax 字词](https://developers.google.com/google-ads/api/performance-max/campaign-criterion-reporting) · [变更记录](https://developers.google.com/google-ads/api/docs/change-event)
