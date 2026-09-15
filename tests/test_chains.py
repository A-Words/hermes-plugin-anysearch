"""Firecrawl->AnySearch chain: normal path + simulated timeout failover.

Run with the Hermes venv's Python (see test_provider.py for the import setup).
"""

from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

try:
    import agent  # noqa: F401
except ImportError:
    _repo = os.environ.get('HERMES_AGENT_REPO')
    if _repo:
        sys.path.insert(0, _repo)

import provider as P  # noqa: E402

URL = 'https://example.com'

print('A. capability matrix (firecrawl-anysearch is extract-only by design)')
fc = P.FirecrawlAnySearchProvider()
print('   %-22s search=%s extract=%s' % (fc.name, fc.supports_search(), fc.supports_extract()))

print('B. normal path — Firecrawl serves the request')
docs = asyncio.run(fc.extract([URL]))
d = docs[0] if docs else {}
print('   ok=%s chars=%d rescued_from=%s' % (
    not d.get('error'), len(d.get('content') or ''), (d.get('metadata') or {}).get('rescued_from')))


class _BrokenFirecrawl:
    def is_available(self):
        return True

    async def extract(self, urls, **kwargs):
        raise RuntimeError('Scrape timed out after 60s (simulated)')


print('C. simulated Firecrawl timeout — AnySearch must take over')
fc._fc = _BrokenFirecrawl()
docs = asyncio.run(fc.extract([URL]))
d = docs[0] if docs else {}
meta = d.get('metadata') or {}
print('   ok=%s chars=%d rescued_from=%s' % (
    not d.get('error'), len(d.get('content') or ''), meta.get('rescued_from')))
print('   backend_error=%s' % str(meta.get('backend_error'))[:110])
