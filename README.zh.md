# hermes-anysearch

给 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 接入
[AnySearch](https://www.anysearch.com) 搜索与网页提取，并附带两条实时兜底链路：
主后端失败时，同一次调用自动由 AnySearch 接管。

| Provider | 作用 | 配置项 |
|---|---|---|
| `anysearch` | 纯 AnySearch（REST `/v1/search` + `/v1/extract`） | `web.backend` 或按能力配置 |
| `exa-anysearch` | Exa 优先，任何 Exa 失败自动转 AnySearch | `web.search_backend` |
| `firecrawl-anysearch` | Firecrawl 优先，任何 Firecrawl 失败自动转 AnySearch | `web.extract_backend` |

## 为什么做兜底链

Hermes 内置的运行时 rescue 只能救到限流严重的匿名免费环。AnySearch 免费额度
**1000 次/日**（带 key 20 QPS，无 key 也可匿名使用），很适合做第二后端：

- **搜索**：Exa 免费额度在集中使用下会耗尽；链路每次调用都先试 Exa，
  额度重置后自动恢复，无需人工切回。
- **提取**：重放 20 条曾在 Firecrawl 上失败的 URL，失败大多来自后端级原因
  （未配置、60s 抓取超时）而非难爬页面，AnySearch 救回其中 65%。
  Firecrawl 仍排第一——它能突破 AnySearch 拿不到的反爬站（npmjs、微信公众号、z-lib）。

## 安装

1. 克隆到 Hermes 插件目录：

   ```bash
   git clone https://github.com/<owner>/hermes-anysearch "$HERMES_HOME/plugins/anysearch"
   ```

2. 启用插件（Hermes 的用户插件默认不启用）：

   ```bash
   hermes plugins enable anysearch
   ```

3. 建议配置 AnySearch API Key（免费 key 可把额度提到 1000 次/日，在
   <https://www.anysearch.com/console/api-keys> 获取）：

   ```env
   # $HERMES_HOME/.env
   ANYSEARCH_API_KEY=as_sk_...
   ```

4. 把 web 工具指向兜底链（也可用 `hermes tools` 选择）：

   ```yaml
   # $HERMES_HOME/config.yaml
   web:
     search_backend: exa-anysearch
     extract_backend: firecrawl-anysearch
   ```

5. 完全退出并重启 Hermes 桌面端（插件与配置在启动时加载）。

### 多 profile

插件目录是 per-profile 的（`$HERMES_HOME/plugins`）。不要给每个 profile 复制一份
（必然漂移），而是让其他 profile 用目录联接（Windows junction / Unix symlink）
指向同一份代码：

```bash
python scripts/link-to-profiles.py           # 建立/修复联接
python scripts/link-to-profiles.py --verify  # 仅检查
```

Hermes 的插件扫描器能识别联接，一份代码即可服务所有 profile。
注意 `plugins.enabled` 与 `.env` 仍是 per-profile 的：新 profile 仍需
`hermes plugins enable anysearch` 和独立配置的 `ANYSEARCH_API_KEY`。

## 垂直搜索与能力查询

启用插件后，在支持 `register_cli_command` 和 `register_skill` 的 Hermes 中运行：

```bash
hermes anysearch domains --domain code
hermes anysearch domains --domain code --domain finance
hermes anysearch search "Go context cancellation documentation" --tag code.doc --params '{"library":"golang"}' --limit 5 --language en
```

先查询能力定义，再选择返回的 `sub_domain` 及其参数。
`--tag`、`--params`（JSON 对象）、`--zone cn|intl` 和 `--language` 都是可选项。
命令输出 JSON；运行失败退出码为 1，参数错误为 2。搜索结果位于 `data.web`，
能力定义位于 `data.domains`；未知领域返回空列表属于正常响应。命令直接调用 AnySearch。

在 Hermes 会话中，让 Agent 加载 `skill_view("anysearch:search")` 后执行流程。
插件 Skill 需要显式加载，不会进入默认的可用 Skill 索引。
执行命令的终端环境必须安装 Hermes 和此插件。命名 Profile 使用
`hermes -p PROFILE anysearch ...`，凭据沿用该 Profile 的 `ANYSEARCH_API_KEY` 配置。
缺少上述注册接口的旧版 Hermes 仍可使用原有 Web Provider。

接口映射与验证命令见[开发说明](docs/development.md)。

## 实测要点

- `/v1/search` 同时返回 `snippet` 和 `content`，但 `content` 是摘要：
  传 `format: "markdown"` 后是结构化摘要（约为默认长度的 2 倍），仍非完整正文，
  完整正文需要 `/v1/extract`。本插件请求 `format: "markdown"` 并把 `content`
  映射为结果描述。
- `POST /v1/extract` 是官方 REST 端点，实测比 MCP `extract` 快约一倍
  （同页 0.45s vs 0.91s）。MCP 路径仅作回退；其 `result.content[].text`
  是一层 JSON 字符串，需要二次解析。
- 带 `Authorization` 头但 key 无效时，API 返回 401/403，不会静默回退到匿名模式。

## 测试

离线客户端回归（Python 环境安装 `httpx` 即可，无需 Hermes、API Key 或联网）：

```bash
python -m unittest discover -s tests -p test_client.py -v
```

共享客户端 `client.py` 会校验 REST 业务码和响应结构。空搜索列表属于正常结果，
缺少结果字段或提取正文为空则报错。仅在网络异常、HTTP 404/405/408、5xx，
或 REST 内容格式错误、正文为空时尝试 MCP；其他 HTTP 客户端错误和业务失败
不触发 MCP 回退。MCP 协议错误和工具错误不会被当作网页正文。
错误诊断省略响应正文，仅保留有效 UUID 请求 ID，避免泄露额度错误中可能包含的凭据。

以下现有脚本是需要联网的冒烟检查。
用 Hermes 的 venv Python 运行：

```bash
<hermes-agent>/venv/Scripts/python tests/test_provider.py    # provider 与模拟故障转移
<hermes-agent>/venv/Scripts/python tests/test_chains.py      # 提取链
<hermes-agent>/venv/Scripts/python tests/test_e2e.py normal  # 走 tools.web_tools 全链路
<hermes-agent>/venv/Scripts/python tests/test_e2e.py broken  # 真实失败 → 救援
```

## License

MIT
