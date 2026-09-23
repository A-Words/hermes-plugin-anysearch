# Development

## Integration boundaries

`client.py` owns AnySearch HTTP requests and safe response handling. The caller
supplies a credential callback, evaluated for each request. `provider.py` adapts
this client to Hermes web search/extraction. `cli.py` exposes advanced search
arguments through `ctx.register_cli_command`; `__init__.py` also registers the
read-only `anysearch:search` skill using `ctx.register_skill`.

The web provider keeps `search(query, limit)`. Advanced options are keyword-only
on the client and exposed by `hermes anysearch search`. No provider-wide tag is
stored: each search explicitly selects its routing. CLI commands return 0 for
success and 1 for runtime failures; argparse rejects invalid syntax with 2.

| Entry point | API | Mapping |
| --- | --- | --- |
| `search(query, limit, *, tag, params, zone, language)` | `POST /v1/search` | `limit` becomes `max_results`; supplied advanced fields are passed through; absent fields are omitted |
| `sub_domains(domains)` | `GET /v1/sub-domains` | Repeated `domain` query parameters; returns the server's capability and parameter definitions |

Search results retain the Hermes `success` / `data.web` shape. Domain discovery
returns `success` / `data.domains`. A successful empty list is valid, but is not
guaranteed for unknown domains. Some unsupported domain queries have returned
HTTP 502 in live checks. Preserve that failure; do not translate it into an empty
list or infer that the domain does not exist.
The plugin validates basic shapes but does not freeze the service's evolving
list of tags or capability-specific parameter requirements into a local enum.
Consult capability definitions before constructing a vertical search.

The CLI calls AnySearch directly and does not invoke Hermes web-tool rescue.
The bundled skill is explicitly loaded with `skill_view("anysearch:search")`.
It requires a terminal environment containing Hermes, the enabled plugin and the
intended profile. It does not install software or move credentials automatically.
Registration is capability-checked so older Hermes hosts retain the web providers.

## Offline checks

Install `httpx` in the development Python environment, then run from the plugin root:

```bash
python -m unittest discover -s tests -p test_client.py -v
python -m unittest discover -s tests -p test_vertical.py -v
```

These tests mock HTTP and need no API key. Existing `test_provider.py`,
`test_chains.py`, and `test_e2e.py` are separate live smoke scripts; do not use
unrestricted test discovery when only offline checks are intended.

For a host integration check, load the plugin in Hermes, verify
`hermes anysearch --help`, query domain definitions and perform a tagged search.
Load the skill in a conversation and check that the agent uses the returned
capability definitions. Verify the intended profile and inspect the command's
JSON error output as well as its exit status.

## API references

- [Search](https://anysearch.com/docs/api-endpoints/v1-search)
- [Sub-domain definitions](https://anysearch.com/docs/api-endpoints/v1-sub-domains)
