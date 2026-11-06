# 从取数到交付

这是给 Codex 的执行说明。用户只表达问题，不需要制作 JSON。先按取数指引取得实际回应；数据不足时交付可确认部分，不能用示例补齐。

## 1. 保存与适配

在用户项目新建 `ads-analysis/<日期-任务>/`。保存原始 MCP 回应、实际查询参数和读取时间；不要保存凭据。工具返回 `isError`、无效 JSON、未知结构或缺字段时处理实际错误，不能当空行。

`scripts/mcp_adapter.py` 支持 `structuredContent.result` 平面字段，也支持 content 中唯一一段同结构 JSON。其他 MCP 格式先检查并显式适配，不能猜字段。适配说明是 `metadata` 加 `mapping`，并保存实际 MCP 参数为同级 `request`。原始行里的账户、币种、时区、日期和资源名在裁剪前核验；没有行级身份时，必须有与 metadata 一致的单账户 request。不能手写虚构请求替代实际调用。

```json
{
  "metadata": {
    "schema_version": "0.2", "evidence_id": "account-current",
    "source": "google_ads_mcp", "customer_id": "实际客户ID",
    "currency": "CNY", "timezone": "Asia/Shanghai",
    "period": {"start": "实际起日", "end": "实际止日", "date_basis": "interaction_date"},
    "retrieved_at": "实际带时区读取时间", "resource": "customer",
    "grain": ["customer_id"], "metrics": ["cost_micros", "clicks", "impressions", "conversions", "conversions_value"],
    "completeness": "complete", "visibility": "platform_reported", "limit": 10000
  },
  "mapping": {
    "customer_id": "customer.id", "cost_micros": "metrics.cost_micros",
    "clicks": "metrics.clicks", "impressions": "metrics.impressions",
    "conversions": "metrics.conversions", "conversions_value": "metrics.conversions_value"
  }
}
```

上例是结构说明，所有值必须来自当前用户与真实查询。没有取完时 `completeness` 标 partial。int64 字符串只在明确整数指标上转换；缺键报错，null 保留。

```text
python3 <总入口目录>/scripts/mcp_adapter.py <回应.json> <适配说明.json> <新标准表.json>
```

字段映射示例：`campaign.id→campaign_id`、`campaign.name→campaign_name`、`campaign.status→status`、`campaign.advertising_channel_type→channel`、`campaign_search_term_view.search_term→term`。所有 metrics 保留原值；micros 不提前除。分片/日/设备行先校验实际完整键，再按目标键求和；不要删掉重复行来掩盖分段。

## 2. 构造当前任务输入

字段类型与嵌套结构见 [输入字段说明](input-fields.md)。本包不附带测试数据，所有输入按真实响应整理。顶层 `bundle_version` 为 `0.2`，不是 Skill 版本。`as_of` 为本次报告证据截止时刻（带时区）；所有表和嵌套配置的读取时间不得晚于它。它通常是本轮最后一次读取完成的时间，不是统计期的结束日。

`mode` 默认 compare，要求双期四张对账表。只审搜索词时用 search_terms，只审转化时用 measurement，这两种专项模式只需当期账户/系列两张轻量对账表及相应专题数据，不强制补上一期。不要为让单期任务通过而复制一份假比较期。用户选择不等长时期时分别用 single_period 模式给出单期结果，说明总量不能直接相比；不要偷偷改用户日期。

| 键 | 内容与约束 |
|---|---|
| datasets.account_current / account_previous | 独立客户总量；grain 为 `[customer_id]`；同币种时区、同口径、等长不重叠窗口 |
| datasets.campaigns_current / campaigns_previous | 期间系列明细；grain 为 `[campaign_id]`；与对应总量同窗口；保留当前暂停系列 |
| datasets.terms_current（可选） | 系列 + 原始字词；grain 为 `[campaign_id,term]`，不含关键词分段 |
| datasets.terms_history（可选） | 同粒度历史，开始早于本期；补拉候选的更长窗口。未返回词保留未知，不填零；不能查未来结果作为当时依据 |
| business_context（可选） | evidence_id、customer_id、confirmed、description、protected_terms；confirmed 只在用户或已有可靠项目资料已确认时为 true |
| negative_inventory（可选） | evidence_id、customer_id、retrieved_at、campaign_ids、complete、layers_checked 及 negatives/shared_lists/attachments/positive_keywords 四表，结构见 [输入字段说明](input-fields.md) |
| term_reviews（可选） | 每行 campaign_id、原始 term、business_fit（core/outside/unknown）、business_quote、rationale。outside 必须有真实业务描述中的依据，不使用通用黑名单 |
| measurement（可选） | 带 evidence_id、customer_id、currency、timezone、period、retrieved_at、configuration_retrieved_at、configuration_complete、performance_complete。独立配置 configured 与表现 reported 两表；以动作 ID 关联；goal_evidence 单独保留自定义目标及系列应用。缺元数据/目标时不能称全量核验 |
| findings（交付前补写） | 最多三个优先问题，每项 title、fact、interpretation、next_step、evidence_refs；交付时还需 why_it_matters（为什么要管）、owner（谁负责）、expected_benefit（预期帮助）、success_check（如何检查效果）、decision_boundary（何时先补证据）。步骤用清楚的自然语言，引用须对应本次证据，预期帮助不能写成已实现收益 |

negative_inventory 的五层是 `account,campaign,ad_group,shared_lists,positive_keywords`。完整表示五层查询均完成，不是只存在一个空数组。共享清单还须有启用状态与实际系列挂载；广告组规则不能冒充全系列覆盖。当前配置不证明历史生效。本套对待审建议采用明确的 24 小时库存新鲜度策略：任何库存读取时间距 as_of 超过 24 小时就要求刷新并降为待确认；这不保证 24 小时内配置不会变化。脚本只做保守字面匹配，不模拟大小写之外的 Google 拼写匹配、语义扩展、复杂符号或超过 16 词的条件。

对 positive_keywords 只提供“完全相同文本”的保护提示，不能据此宣称已完成所有语义冲突检查。Codex 仍需检查正向词、主营业务和可能的采购场景。PMax 否定仅涉及 Search/Shopping。

历史有转化、历史缺行、业务未知、范围不完整、当前有转化、已有部分覆盖或匹配不确定时，不自动产出可执行否定项。当前与历史 all_conversions 缺失或大于零也须先拆动作核验，不能因主转化为零忽视其余信号。`REVIEW_ONLY` 也只是待审，`execution_allowed` 永远为 false。CSV 同时保留系列名称、当前和历史花费/点击/两类转化及历史日期；历史含本期时明确说明，不把两个窗口相加。

## 3. 生成与检查

```text
python3 <总入口目录>/scripts/report_pipeline.py <本次bundle.json> <新的输出目录>
```

输出目录必须是新的，保护旧报告。产物：

- `report.html`：可直接打开的中文 HTML，无网络字体/远程脚本。
- `negative-review.csv`：全部搜索字词的待审状态、范围、证据和理由；不是 Google Ads 批量导入文件。
- `measurement-review.csv`：配置与表现覆盖、固定价值、自定义目标关系。
- `analysis.json`：核对结果、词组观察及证据索引。
- `receipt.json`：完成状态与输出文件哈希，便于识别后续改动。

核对全部共同可加指标（整数精确对齐，归因小数用容差），并检查可见搜索字词不超过完整的所属系列；配置中已停用但本期有表现的动作也保留。检查总量差额、缺数与截断警告；处理影响结论的错误后，再填写 findings 重生成到新目录。HTML 只显示前 200 条明细，CSV/JSON 保留全部本次输入结果；不能把前 200 行当全量。词组互相重叠，不加总；中文或复杂符号未自动拆词，需单独语义分析。

报告生成器是底稿和核对工具，不会自动猜业务、写因果结论或证实真实 ROI。没有 findings 时页面明确显示“自动核对底稿”，不能作为完整诊断交差。需要设备/地域等下钻时单独读取、校验并将实际发现写入 findings，不把多个切片加成总量。每个下钻表另核对适用指标；保留读取时刻差异和非闭合项，不因核心四表通过就声称所有切片对齐。特别是网址表现不能擅自把展示数相加当账户展示；本期末刚过午夜的数据仍可能回补。HTML 应优先展示有当期/对比期数据的系列与可行动的词，完整输入继续保留。

旧输入仍兼容，新增说明字段缺失时页面标“行动说明待补充”，不能靠脚本自动猜负责人或承诺好处。交付前由 Codex 按 [通俗交付要求](deliverables.md) 补写，主页面不裸露数据键名、状态码或长技术表。

默认渲染器给每条 findings 生成可复制的只读继续任务；任务文字仍来自已经核实的 next_step，不能写成自动执行广告变更。金额与次数保持完整，长表提供有界滚动，窄屏显示带字段名的卡片。自定义 HTML 也要遵守这些要求；有搜索筛选时补清空入口、结果计数和空结果说明。

最后用 Codex Computer Use 在用户常用浏览器打开最终交付路径，检查顶部结论、行动、明细、页尾，实点复制、筛选与下载；放大或窄屏检查后恢复原显示。没有 Computer Use 时引导开启，不安装浏览器替代工具。未能看到正文或操作成功，就不能把视觉/交互标通过。交付 HTML 链接、两份 CSV 和最多三个优先动作，不让用户阅读技术日志。

## 出错时

| 观察 | Codex 应做什么 |
|---|---|
| 时区找不到 | 核对 IANA 名；正确但缺时区库时为实际 Python 环境装 tzdata，不改成随意时区 |
| 重复 grain | 查漏出的日/设备等分段，先核对真实粒度；不能直接 drop duplicates |
| 跨账户/币种/日期 | 重新组织对应任务，不强行合表或编汇率 |
| limit 达到上限 | 按互斥片继续取；无法取完就留 partial |
| 无 history / 当前规则 / 业务证据 | 已有表现照常说明，相应否定项留待确认 |
| findings 引用不存在 | 纠正引用并核事实；不能创建虚构证据对象凑数 |
| 安装有同名不同内容 | 保留用户版本、比较并备份后处理，不覆盖或删除全局 Skills |
