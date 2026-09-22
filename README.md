# hermes-anysearch

[AnySearch](https://www.anysearch.com) web search and content extraction for
[Hermes Agent](https://github.com/NousResearch/hermes-agent), plus two real-time
fallback chains that keep the web tools working when a primary backend fails.

| Provider | What it does | Select with |
|---|---|---|
| `anysearch` | AnySearch alone (REST `/v1/search` + `/v1/extract`) | `web.backend` or per-capability keys |
| `exa-anysearch` | Exa first → AnySearch on any Exa failure | `web.search_backend` |
| `firecrawl-anysearch` | Firecrawl first → AnySearch on any Firecrawl failure | `web.extract_backend` |

## Why the chains

Hermes' built-in runtime rescue only rescues a failed call to its keyless
anonymous ring, which is heavily throttled. AnySearch's free tier is
**1,000 requests/day** (20 QPS with a key; anonymous access works without one),
which makes it a strong second backend:

- **Search**: Exa's free credits drain under burst use; the chain retries Exa on
  every call, so a reset quota is picked up automatically with no manual switch-back.
- **Extract**: in a batch replay of 20 URLs that had previously failed on
  Firecrawl, most failures were backend-level (unconfigured, 60s scrape
  timeouts) rather than hard pages, and AnySearch recovered 65% of them.
  Firecrawl stays first because it clears anti-bot walls AnySearch cannot
  (npmjs, WeChat, z-lib).

## Install

1. Clone into your Hermes plugin directory:

   ```bash
   git clone https://github.com/<owner>/hermes-anysearch "$HERMES_HOME/plugins/anysearch"
   ```

2. Enable the plugin (Hermes requires explicit opt-in for user plugins):

   ```bash
   hermes plugins enable anysearch
   ```

3. Optional but recommended — add an AnySearch API key. A free key raises the
   quota to 1,000 requests/day; get one at
   <https://www.anysearch.com/console/api-keys>.

   ```env
   # $HERMES_HOME/.env
   ANYSEARCH_API_KEY=as_sk_...
   ```

4. Point the web tools at the chains (or pick them via `hermes tools`):

   ```yaml
   # $HERMES_HOME/config.yaml
   web:
     search_backend: exa-anysearch
     extract_backend: firecrawl-anysearch
   ```

5. Fully restart Hermes Desktop — plugins and config load at startup.

### Multiple profiles

Plugins are per-profile (`$HERMES_HOME/plugins`), so naively this directory
would need a copy per profile and drift. Instead, make every other profile a
directory junction (Windows) or symlink (Unix) to the single checkout:

```bash
python scripts/link-to-profiles.py           # create/repair links
python scripts/link-to-profiles.py --verify  # check only
```

Hermes' plugin scanner follows junctions/symlinks, so one checkout serves every
profile. Note that `plugins.enabled` and `.env` stay per-profile: a new profile
still needs `hermes plugins enable anysearch` and its own `ANYSEARCH_API_KEY`.

## Vertical search and capability discovery

With the plugin enabled, use a Hermes version that supports
`register_cli_command` and `register_skill` to access the advanced workflow:

```bash
hermes anysearch domains --domain code
hermes anysearch domains --domain code --domain finance
hermes anysearch search "Go context cancellation documentation" --tag code.doc --params '{"library":"golang"}' --limit 5 --language en
```

Discover definitions first, then choose a returned `sub_domain` and its parameters.
`--tag`, `--params` (JSON object), `--zone cn|intl`, and `--language` are optional.
The commands print JSON; runtime errors exit 1, argument errors exit 2. Searches
return `data.web`; capability discovery returns `data.domains`. An empty domain
list is a valid response for unknown domains. Commands call AnySearch directly.

In a Hermes conversation, ask the agent to load
`skill_view("anysearch:vertical-search")` and follow the workflow. Plugin skills
require explicit loading and do not appear in the default available-skills index.
Terminal execution requires Hermes and this plugin in that terminal environment.
For a named profile, use `hermes -p PROFILE anysearch ...`; the commands use the
profile's normal `ANYSEARCH_API_KEY` lookup. Older Hermes versions without these
registration APIs retain the existing web providers only.

See [development notes](docs/development.md) for the API mapping and validation commands.

## Notes from testing against the live API

- `/v1/search` returns both `snippet` and `content`, but `content` is an
  abstract. With `format: "markdown"` it becomes a structured digest (roughly
  2x the default length), never the full page — full text requires
  `/v1/extract`. This provider requests `format: "markdown"` and maps `content`
  to the result description.
- `POST /v1/extract` is the official REST endpoint and measured about 2x faster
  than the MCP `extract` tool (0.45s vs 0.91s for the same page). The MCP path
  is kept only as a fallback; its `result.content[].text` is a JSON *string*
  that needs a second parse.
- With an `Authorization` header present but the key invalid, the API returns
  401/403 — it does not silently fall back to anonymous.

## Tests

Offline client regressions (Python with `httpx` installed; no Hermes installation,
API key, or network access required):

```bash
python -m unittest discover -s tests -p test_client.py -v
```

The shared client in `client.py` validates REST business codes and response
shapes before the provider returns success. Empty search result lists are valid;
missing results and empty extracted content are errors. Extraction tries MCP
only after transport failures, HTTP 404/405/408 or 5xx, or malformed/empty REST
content. Other HTTP client errors and unsuccessful business responses do not
trigger MCP fallback. MCP protocol and tool errors are never returned as page
content. Error diagnostics omit response bodies and retain valid UUID request
IDs, because quota error bodies can contain credentials.

The existing scripts below are live smoke checks and require network access.
Run with the Hermes venv Python:

```bash
<hermes-agent>/venv/Scripts/python tests/test_provider.py    # providers + simulated failover
<hermes-agent>/venv/Scripts/python tests/test_chains.py      # extract chain
<hermes-agent>/venv/Scripts/python tests/test_e2e.py normal  # through tools.web_tools
<hermes-agent>/venv/Scripts/python tests/test_e2e.py broken  # real failures → rescue
```

## License

MIT
