"""AnySearch web provider plugin for Hermes.

Registers three web search/extract providers:

* ``anysearch``           — AnySearch alone (REST ``/v1/search`` + ``/v1/extract``).
* ``exa-anysearch``       — Exa first, AnySearch on failure (for ``web.search_backend``).
* ``firecrawl-anysearch`` — Firecrawl first, AnySearch on failure (for ``web.extract_backend``).

Loaded either as a package (relative import works) or by file path (no parent
package, so fall back to the sibling module) — same pattern as browser-obscura.
"""

from __future__ import annotations

try:
    from .provider import (
        AnySearchProvider,
        ExaAnySearchProvider,
        FirecrawlAnySearchProvider,
    )
except ImportError:  # pragma: no cover - path-load fallback
    from provider import (  # type: ignore[no-redef]
        AnySearchProvider,
        ExaAnySearchProvider,
        FirecrawlAnySearchProvider,
    )


def register(ctx) -> None:
    """Called by the Hermes plugin system on load."""
    ctx.register_web_search_provider(AnySearchProvider())
    ctx.register_web_search_provider(ExaAnySearchProvider())
    ctx.register_web_search_provider(FirecrawlAnySearchProvider())
