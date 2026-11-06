# 数据入口与口径

先读实际工具说明。官方 Skill 的方法可用，但示例 `query` 不一定是当前 MCP 参数；常见形式为 resource/fields/conditions/orderings/limit。先发现账户，再查资源元数据，字段可用才查询。

| 对象 | 查询方向 | 注意 |
|---|---|---|
| 总计/系列 | customer / campaign | 日期 BETWEEN；micros；币种时区 |
| 搜索词 | search_term_view / campaign_search_term_view | 可能含多类型，按系列映射；不是名字带 campaign 就都是 PMax |
| 转化设置 | conversion_action | 全库存与当期表现分开 |
| 目标使用 | customer/campaign conversion goal、custom goal、goal config | 依元数据选字段，不只看 primary_for_goal |
| 动作表现 | 单独按 conversion_action 分段 | 与费用等指标兼容性先查，必要时分查后分别汇总 |
| 否定范围 | ad_group_criterion、campaign_criterion、shared_set/criterion、关联、账户列表 | 包括适用关系 |
| 改动 | change_event / change_status | 当前官方时间范围与 LIMIT，空记录不是无人改过 |
| GA4 | Admin 属性、Data run_report | 工具 protobuf 可能 snake_case，API 文档为 camelCase |
| GSC | Search Analytics | MCP dimensions 可能逗号字符串；总计另查无维度 |

403 区分权限、API、字段与范围，不靠反复重登/扩权解决。缺分页或最终状态时用已有获准 API/导出补验，否则标部分覆盖。不能扫描其他项目凭据、覆盖共享 ADC。

先小范围验证连接和独立总计，再按问题扩大。保留请求、响应、捕获时间、哈希、身份/过滤、日期口径、分页/limit、元数据、返回数。哈希证明本地文件未变，不证明来源或归因正确。原始与规范化副本分开，适配前核对请求条件。

明确区分本次读取、历史快照、合成测试、未覆盖。UI/API 对账用同账户/范围/时区/列/日期/精度。

没连接时一次给一个最小导出任务：先系列报表（账户、日期、系列 ID/名/类型、展示、点击、费用、Conversions/All conversions/Value、币种时区），按需再补目标与销售反馈。不一次索要十份表，不要求小白部署网站或新平台。
