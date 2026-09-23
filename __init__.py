"""AnySearch web provider plugin for Hermes.

Registers three web search/extract providers:

* ``anysearch``           — AnySearch alone (REST ``/v1/search`` + ``/v1/extract``).
* ``exa-anysearch``       — Exa first, AnySearch on failure (for ``web.search_backend``).
* ``firecrawl-anysearch`` — Firecrawl first, AnySearch on failure (for ``web.extract_backend``).

Loaded either as a package (relative import works) or by file path (no parent
package, so fall back to the sibling module) — same pattern as browser-obscura.
"""

from __future__ import annotations

from pathlib import Path

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
    if __package__:
        from .cli import setup_parser, handle_command
    else:
        from cli import setup_parser, handle_command
    # Older Hermes versions can still use the web providers.
    if callable(getattr(ctx, "register_cli_command", None)):
        ctx.register_cli_command("anysearch", "Search AnySearch and discover domain capabilities",
                                 setup_parser, handle_command)
    if callable(getattr(ctx, "register_skill", None)):
        ctx.register_skill("search", Path(__file__).parent / "skills" / "search" / "SKILL.md",
                           description="Discover AnySearch capabilities and run individual or parallel searches")
