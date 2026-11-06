# 输入字段说明

这是给 Codex 的适配合同，用户不需要手写 JSON。本文件只说明结构，不含账户、报表样本或测试数据。工作目录和输出方式见 [报告流程](pipeline.md)。

## 标准数据表 dataset

| 字段 | 类型与含义 |
|---|---|
| schema_version | 字符串，`0.1` 或 `0.2` |
| evidence_id | 当前任务中唯一的证据 ID |
| source | 使用连接读取时为 `google_ads_mcp`，使用用户导出时为 `user_export`；实际工具名称与请求保存在原始证据中 |
| customer_id | 已核对的当前客户号，字符串 |
| currency / timezone | 实际账户币种与 IANA 时区 |
| period | 对象，含真实 start、end（YYYY-MM-DD）及 date_basis（interaction_date / conversion_date）；比较期口径必须一致 |
| retrieved_at | 带时区偏移的实际读取时间 |
| resource | 实际查询资源 |
| grain | 唯一键字段名数组，包含所有实际分段 |
| metrics | 已取得的可加指标名数组；允许 cost_micros、clicks、impressions、conversions、all_conversions、conversions_value |
| completeness | complete、partial 或 unknown，依据实际查询覆盖判定 |
| visibility | platform_reported、partial 或 unknown |
| limit | 实际请求的行数上限；未设置时如实省略，不编造 |
| rows | 行对象数组；每行含 grain 字段以及 metrics 对应值。整数指标保留精确整数，未知保留 null |

dataset 的完整合法值以 [evidence_guard.py](../scripts/evidence_guard.py) 的校验为准。不能为了通过检查改变真实日期或补造零值。字段映射及双期账户/系列表的组合见 [pipeline.md](pipeline.md)。

## 否定规则 negative_inventory

顶层包括 evidence_id、customer_id、retrieved_at、campaign_ids、complete、layers_checked。可保留已核实的 currency、timezone；所有嵌套配置必须属于同一账户。layers_checked 表示实际读取过的层级：account、campaign、ad_group、shared_lists、positive_keywords。

下列四个字段都是数组；读取完成且确实没有记录时才是空数组。没有权限或没读到不等于完整空表。

| 数组 | 每行结构 |
|---|---|
| negatives | id、text、match_type、status、scope。scope 为 account / campaign / ad_group / shared_list；系列/广告组规则带 campaign_id，广告组规则另保留 ad_group_id，共享规则带 list_id |
| shared_lists | id、status；保存实际列表状态 |
| attachments | list_id、campaign_id、status；表示共享列表与系列的实际挂载关系 |
| positive_keywords | id、campaign_id、text；保留实际匹配、状态等原始字段供语义审查 |

match_type 保留 EXACT / PHRASE / BROAD；status 依据原始配置映射。字面检查只使用已启用规则，不能模拟 Google 的完整匹配引擎。历史、业务确认及作用域不足时保留待确认。

## 转化核验 measurement

顶层字段为 evidence_id、customer_id、currency、timezone、period、retrieved_at、configuration_retrieved_at、configuration_complete、performance_complete。配置和表现是独立读取，读取时间不能晚于 bundle.as_of。

| 字段 | 结构 |
|---|---|
| configured | 每个动作一行，必有 id、status、primary。按实际响应补 name、category、origin、type、counting_type、click_through_lookback_window_days、attribution_model_settings.attribution_model、always_use_default_value、default_value |
| reported | 每个动作 ID 一行，含 id 与实际返回的 conversions、all_conversions、conversions_value；不混入花费。未返回的动作不补零 |
| goal_evidence | 保留 evidence_id、读取时间及目标证据；custom_goals 每行含 id、actions（实际动作 ID 数组）；campaign_goals 每行含 campaign_id、custom_goal_id（未应用时按真实状态保留空值） |

标准目标、目标层级与价值规则等原始证据也应保留在 goal_evidence 或对应私有证据中。脚本中自定义目标关联表不能替代完整的目标核验；须由 Codex 按 [转化核验流程](../../google-ads-measurement/SKILL.md) 判断标准目标与自定义目标并用情况。缺少元数据、权限或配置时不能宣称全量核验。

## 业务判断与报告

business_context、term_reviews、findings 的字段见 [报告流程](pipeline.md)。confirmed 只能来自用户或已核实业务资料。程序检查结构与计算，不能自动证明人工判断正确。
