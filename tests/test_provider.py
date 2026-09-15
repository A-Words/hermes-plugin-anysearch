"""Unit checks: provider surface, normal path, simulated failover, extract unwrap.

Run with the Hermes venv's Python (so ``agent`` / ``hermes_cli`` are importable):

    <hermes-agent>/venv/Scripts/python tests/test_provider.py

If those modules are not importable, point ``HERMES_AGENT_REPO`` at your
hermes-agent checkout. AnySearch's anonymous tier works without a key; set
``ANYSEARCH_API_KEY`` (a real key) for production rates.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))  # plugin root, so `import provider` works

try:
    import agent  # noqa: F401  (already importable inside the Hermes venv)
except ImportError:
    _repo = os.environ.get('HERMES_AGENT_REPO')
    if _repo:
        sys.path.insert(0, _repo)

import provider as P  # noqa: E402

URL = 'https://example.com'

print('A. provider surface')
for cls in (P.AnySearchProvider, P.ExaAnySearchProvider, P.FirecrawlAnySearchProvider):
    p = cls()
    print('   %-20s search=%-5s extract=%-5s available=%s' % (
        p.name, p.supports_search(), p.supports_extract(), p.is_available()))

print('B. AnySearch search (format=markdown: description should exceed ~150 chars)')
pure = P.AnySearchProvider()
resp = pure.search('Hermes Agent web search backends', 3)
hits = (resp.get('data') or {}).get('web') or []
print('   success=%s results=%d' % (resp.get('success'), len(hits)))
for h in hits[:3]:
    print('   desc %3d chars | %s' % (len(h['description']), h['description'][:62].replace('\n', ' ')))

print('C. Exa->AnySearch chain, normal path (should use Exa when reachable)')
chain = P.ExaAnySearchProvider()
resp = chain.search('Hermes Agent', 3)
print('   success=%s rescued_from=%s' % (resp.get('success'), (resp.get('data') or {}).get('rescued_from')))


class _BrokenExa:
    def is_available(self):
        return True

    def search(self, q, l):
        raise RuntimeError('402 Payment Required (simulated)')

    def extract(self, urls):
        raise RuntimeError('402 Payment Required (simulated)')


print('D. Exa->AnySearch chain, simulated Exa failure (must rescue via AnySearch)')
_real = chain._exa
chain._exa = _BrokenExa()
resp = chain.search('python asyncio tutorial', 3)
data = resp.get('data') or {}
print('   success=%s results=%d rescued_from=%s' % (
    resp.get('success'), len(data.get('web') or []), data.get('rescued_from')))
chain._exa = _real

print('E. AnySearch extract (MCP wrapper JSON must be unwrapped, not returned raw)')
docs = pure.extract([URL])
d = docs[0] if docs else {}
content = d.get('content') or ''
print('   error=%s chars=%d is-wrapper-json=%s' % (
    (d.get('error') or '')[:40], len(content), content.strip().startswith('{"url"')))
