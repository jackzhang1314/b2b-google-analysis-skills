# 外贸 B2B 谷歌数据分析 Skills

把 GA4（谷歌分析）、Google Search Console（谷歌站长）和 Google Ads 的报表，变成看得懂的判断、具体行动和中文 HTML 报告。

适合外贸工厂、贸易公司、网站运营与广告负责人。你只需要告诉 Codex 网站、主营业务、目标客户和想解决的问题。已连接自己的 Google 账户时可以直接取数，也可以使用自己导出的报表。

**[阅读通俗使用教程](docs/使用教程.md)** · **[下载完整安装包](https://github.com/jackzhang1314/b2b-google-analysis-skills/archive/refs/heads/main.zip)**

## 有哪些 Skill

| Skill | 解决什么问题 | 版本 |
|---|---|---|
| [b2b-ga4-gsc](skills/b2b-ga4-gsc/SKILL.md) | 谁在访问网站、什么词带来搜索点击、哪些页面和询盘路径值得改 | 1.0.0 |
| [google-ads-analysis](skills/google-ads-analysis/SKILL.md) | 广告分析总入口；根据问题组织下面三个模块，形成完整报告 | 1.0.3 套件 |
| [google-ads-diagnose](skills/google-ads-diagnose/SKILL.md) | 花费、点击和转化为什么变化，优先查哪个系列、地区或设备 | 1.0.3 套件 |
| [google-ads-search-terms](skills/google-ads-search-terms/SKILL.md) | 实际搜索词是否跑偏，哪些值得保留、补证或人工审查否定 | 1.0.3 套件 |
| [google-ads-measurement](skills/google-ads-measurement/SKILL.md) | 平台转化到底记录了什么，能否代表有效询盘或收入 | 1.0.3 套件 |
| [b2b-google-ads](skills/b2b-google-ads/SKILL.md) | 可选综合入口：结合广告、GA4/GSC 与销售反馈，诊断 B2B 获客 | 1.1.0 |

GA4 和谷歌站长共同使用 `b2b-ga4-gsc`，无需另装两个重复入口。四个 `google-ads-*` Skill 共享脚本与规则，按整套安装。`b2b-google-ads` 是另一种综合广告工作流，通常在两种广告入口中选一个。

## 把这段话发给 Codex 即可安装

先在 Codex 中打开一个用于客户分析的项目文件夹，再发送：

```text
请从 https://github.com/jackzhang1314/b2b-google-analysis-skills 下载这批 Skills。
先阅读 README.md 和 install.py，检查 Python 3.11 或更新版本。
安装到当前这个客户分析项目，使用 standard 组合，不安装到全局。
先执行 --check，再正式安装；已有同名不同内容时保留原版，并告诉我差异。
不要修改 Google 连接、凭据或广告设置。
完成后检查项目中能否发现这 5 个 Skill，并区分“已安装”和“账户已连接”。
```

`standard` 安装网站入口加四个广告模块。只做网站分析，将提示词中的 `standard` 改成 `ga4-gsc`；只做广告，改成 `ads`。想采用综合 B2B 广告入口，选择 `b2b-ads`。

安装程序只复制所选 Skill 到项目 `.agents/skills/`；相同内容重复运行不会重装。发现不同版本会先停止，明确要升级时再使用 `--upgrade`，旧版保存在该项目 `.agents/skill-backups/`。[Codex 官方本地 Skill 说明](https://learn.chatgpt.com/docs/build-skills)

## 装好后怎么用

网站分析：

```text
请用 $b2b-ga4-gsc 分析我的网站。
网址是【网址】，主营【产品或服务】，目标客户是【国家和采购对象】。
我想弄清【流量下降 / 首页关键词 / 有访问但没询盘】。
用已有只读连接读取合适的可比周期，给我通俗 HTML 报告和优先行动。
```

广告分析：

```text
请用 $google-ads-analysis 检查我的广告。
我们主营【业务】，目标市场是【地区】，有效询盘应符合【采购条件】。
重点解决【广告费增加 / 搜索词跑偏 / 转化多但销售说没效果】。
先核对账户和日期，只读分析，说明前三项优先行动、具体步骤和验证方法。
```

## 安装选项

供 Codex 执行，普通用户无需手动操作。下载并解压仓库后，在发行版目录运行：

```bash
python3 install.py --list
python3 install.py --project "../客户网站分析" --profile standard --check
python3 install.py --project "../客户网站分析" --profile standard
```

| profile | 安装内容 |
|---|---|
| `ga4-gsc` | 网站分析，1 个 Skill |
| `ads` | 专项广告分析套件，4 个 Skill |
| `standard`（默认） | 网站分析 + 广告套件，5 个 Skill |
| `b2b-ads` | 综合 B2B 广告入口，1 个 Skill |
| `all` | 全部 6 个；适合需要比较两种广告工作流的人 |

Windows 可由 Codex 使用实际可用的 `py -3` 或 `python`；先确认版本至少为 3.11。脚本使用标准库；缺少 IANA 时区数据时，在项目 Python 环境安装 `tzdata`。安装后在目标项目检查发现结果，未出现时重启 Codex。

## 使用范围

Skill 提供分析方法和本地计算能力，Google 连接负责取数。安装本仓库不会自动登录 Google，也不会给你其他人的账户权限。缺少连接时，Codex 可以先分析你的报表，并列出缺少的数据。

默认只读：生成分析、可审阅建议和操作清单，不自动改预算、出价、否定词、网站或转化设置。平台转化、收到的询盘、销售确认的合格询盘、成交收益分别说明；没有对应证据时不会编出真实 ROI。

本仓库只包含 Skill 正文、必要脚本与参考资料、空白销售反馈表、安装工具和用户教程。不含客户数据、测试数据、内部研究记录、账号配置或凭据。实际报表和分析结果保存在你自己的客户项目中。

更多场景、最少输入、可复制请求、产物和完成检查见 **[使用教程](docs/使用教程.md)**。
