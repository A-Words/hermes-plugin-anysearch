"""End-to-end through tools.web_tools — the real dispatcher and provider resolution.

Requires the plugin to be installed AND enabled under ``$HERMES_HOME``
(``hermes plugins enable anysearch``) and selected in config:

    web:
      search_backend:  exa-anysearch
      extract_backend: firecrawl-anysearch

Usage (Hermes venv Python):

    python tests/test_e2e.py normal    # Exa/Firecrawl serve the calls
    python tests/test_e2e.py broken    # invalid keys injected -> chains must rescue

``web_search_tool`` is SYNC (returns a JSON string); ``web_extract_tool`` is async.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
os.environ.setdefault('HERMES_HOME', os.path.join(os.path.expanduser('~'), '.hermes'))

try:
    import hermes_cli  # noqa: F401
except ImportError:
    _repo = os.environ.get('HERMES_AGENT_REPO')
    if _repo:
        sys.path.insert(0, _repo)

mode = sys.argv[1] if len(sys.argv) > 1 else 'normal'
if mode == 'broken':
    # Force real backend failures (os.environ wins over ~/.hermes/.env).
    os.environ['EXA_API_KEY'] = 'sk-invalid-e2e-verification'
    os.environ['FIRECRAWL_API_KEY'] = 'fc-invalid-e2e-verification'

from hermes_cli.plugins import _ensure_plugins_discovered  # noqa: E402

_ensure_plugins_discovered()

from agent.web_search_registry import (  # noqa: E402
    get_active_extract_provider,
    get_active_search_provider,
)

print('mode: %s' % mode)
print('search  ->', getattr(get_active_search_provider(), 'name', None))
print('extract ->', getattr(get_active_extract_provider(), 'name', None))

from tools.web_tools import web_extract_tool, web_search_tool  # noqa: E402

query = 'Hermes Agent plugin catalog' if mode == 'normal' else 'python dataclasses guide'
raw = web_search_tool(query, 3)
try:
    d = json.loads(raw)
    data = d.get('data') or {}
    print('search: success=%s rescued_from=%s results=%d' % (
        d.get('success'), data.get('rescued_from'), len(data.get('web') or [])))
except Exception as exc:
    print('search: unparseable output (%s): %s' % (exc, str(raw)[:200]))

out = asyncio.run(web_extract_tool(['https://example.com']))
try:
    j = json.loads(out)
    for r in j.get('results') or []:
        print('extract: chars=%d error=%s' % (
            len(r.get('content') or ''), (r.get('error') or '')[:70]))
except Exception as exc:
    print('extract: unparseable output (%s): %s' % (exc, str(out)[:200]))
