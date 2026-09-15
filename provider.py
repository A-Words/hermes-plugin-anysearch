"""AnySearch web search + extract, plus two fallback chains.

Providers registered by ``__init__.register``:

* ``anysearch``           — AnySearch alone (REST ``/v1/search`` + ``/v1/extract``).
* ``exa-anysearch``       — Exa first; on ANY Exa failure the same call is served
  by AnySearch. Selected via ``web.search_backend``.
* ``firecrawl-anysearch`` — Firecrawl first; on ANY Firecrawl failure the same
  call is served by AnySearch. Selected via ``web.extract_backend``.

Why the chains exist (both rates measured against the live APIs, 2026-09):

* Hermes' built-in runtime rescue (``tools/web_tools_rescue.tools``) only
  rescues to the *keyless anonymous ring*, which is heavily throttled. A
  configured provider that fails at the network/quota level otherwise just
  errors out.
* Of 20 historical Firecrawl extract failures, AnySearch recovered **13 (65%)**.
  The failures were dominated by *backend-level* causes (Firecrawl not
  configured / scrape timeouts), not by hard pages — exactly what a second
  backend fixes. Genuinely anti-scraping-resistant pages (npmjs, WeChat,
  z-lib) fail on both, so Firecrawl stays first.

AnySearch API notes, verified live 2026-09:

* ``/v1/search`` accepts ``format: "markdown"``; ``content`` then carries a
  structured Markdown block (~2.2x the length of the default payload, e.g.
  1438 vs 661 chars over 5 results). It is still an *abstract*, never the full
  page — full text needs ``/v1/extract`` (26.6 KB for one docs page).
* ``POST /v1/extract`` is the official REST extract endpoint and is ~2x faster
  than the MCP ``extract`` tool (0.45s vs 0.91s, identical content). The MCP
  route is used only as a fallback: its ``result.content[].text`` holds a JSON
  *string* that needs a second parse.
* ``GET /v1/sub-domains`` does not consume quota (unused here).
* With an Authorization header present but the key invalid, the gateway returns
  401/403 — it never silently falls back to anonymous.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Dict, List

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger(__name__)

ANYSEARCH_API_BASE = "https://api.anysearch.com"
_SEARCH_TIMEOUT = 30.0
_EXTRACT_TIMEOUT = 60.0
# `content` under format=markdown is a structured abstract (title/url + excerpt);
# longer and better-shape than `snippet` (measured 222-345 chars vs 100).
_SEARCH_FORMAT = "markdown"


class _AnySearchClient:
    """Thin REST client for AnySearch. No key required (anonymous tier)."""

    @staticmethod
    def key() -> str:
        """config-aware lookup: os.environ, then ``~/.hermes/.env``."""
        return get_provider_env("ANYSEARCH_API_KEY")

    def _post(self, path: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        import httpx

        headers = {"Content-Type": "application/json"}
        key = self.key()
        if key:
            headers["Authorization"] = "Bearer " + key
        resp = httpx.post(
            ANYSEARCH_API_BASE + path, json=payload, headers=headers,
            timeout=timeout, follow_redirects=True,
        )
        resp.raise_for_status()
        text = (resp.text or "").strip()
        if text.startswith("data:"):  # streamable-HTTP SSE framing (MCP path only)
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return json.loads(text)

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        raw = self._post(
            "/v1/search",
            {
                "query": query,
                "max_results": max(1, min(int(limit or 5), 10)),
                "format": _SEARCH_FORMAT,
            },
            _SEARCH_TIMEOUT,
        )
        results = ((raw.get("data") or {}).get("results")) or []
        hits = []
        for i, r in enumerate(results):
            hits.append({
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                # markdown-mode `content` is the richer field; snippet is the fallback.
                "description": r.get("content") or r.get("snippet") or "",
                "position": i + 1,
            })
        return {"success": True, "data": {"web": hits}}

    def _extract_rest(self, url: str) -> tuple:
        """Official REST extract endpoint — ~2x faster than the MCP tool."""
        raw = self._post("/v1/extract", {"url": url}, _EXTRACT_TIMEOUT)
        d = raw.get("data") or {}
        return d.get("title") or "", d.get("content") or ""

    def _extract_mcp(self, url: str) -> tuple:
        """MCP fallback. NOTE: ``result.content[].text`` is a JSON *string*."""
        import httpx

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        key = self.key()
        if key:
            headers["Authorization"] = "Bearer " + key
        resp = httpx.post(
            ANYSEARCH_API_BASE + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "extract", "arguments": {"url": url}}},
            headers=headers, timeout=_EXTRACT_TIMEOUT, follow_redirects=True,
        )
        resp.raise_for_status()
        text_body = (resp.text or "").strip()
        if text_body.startswith("data:"):
            for line in text_body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    text_body = line[5:].strip()
                    break
        data = json.loads(text_body)
        text = ""
        for item in (data.get("result") or {}).get("content") or []:
            if item.get("type") == "text":
                text = item.get("text") or ""
                break
        title, content = "", text
        try:  # second parse: the MCP text field is itself a JSON document
            inner = json.loads(text)
            if isinstance(inner, dict):
                content = inner.get("content") or text
                title = inner.get("title") or ""
        except (ValueError, TypeError):
            pass
        return title, content

    def extract_one(self, url: str) -> tuple:
        try:
            title, content = self._extract_rest(url)
            if content:
                return title, content
            logger.info("AnySearch REST extract returned empty for %s; trying MCP", url)
        except Exception as exc:  # noqa: BLE001
            logger.info("AnySearch REST extract failed for %s (%s); trying MCP", url, exc)
        return self._extract_mcp(url)

    def extract(self, urls: List[str]) -> List[Dict[str, Any]]:
        docs = []
        for url in urls:
            try:
                title, content = self.extract_one(url)
                docs.append({
                    "url": url, "title": title, "content": content,
                    "raw_content": content, "metadata": {"sourceURL": url},
                })
            except Exception as exc:  # noqa: BLE001 — per-URL failure shape
                logger.warning("AnySearch extract failed for %s: %s", url, exc)
                docs.append({
                    "url": url, "title": "", "content": "", "raw_content": "",
                    "error": str(exc), "metadata": {"sourceURL": url},
                })
        return docs


class AnySearchProvider(WebSearchProvider):
    """AnySearch alone: agent-native search with 17 vertical domains."""

    def __init__(self) -> None:
        self._client = _AnySearchClient()

    @property
    def name(self) -> str:
        return "anysearch"

    @property
    def display_name(self) -> str:
        return "AnySearch"

    def is_available(self) -> bool:
        """Always available: the anonymous tier works without a key (lower quota)."""
        return True

    def is_keyless_available(self) -> bool:
        """Not a keyless-ring vendor — keep it out of the anonymous free-tier walk."""
        return False

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            logger.info("AnySearch search: '%s' (limit=%d)", query, limit)
            return self._client.search(query, limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("AnySearch search error: %s", exc)
            return {"success": False, "error": f"AnySearch search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        return self._client.extract(urls)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "AnySearch",
            "badge": "free · agent-native",
            "tag": (
                "Agent-native search with 17 vertical domains and 1,000 free "
                "requests/day. Key optional — anonymous access works at lower limits. "
                "Set ANYSEARCH_API_KEY to raise the quota."
            ),
            "env_vars": [{
                "key": "ANYSEARCH_API_KEY",
                "prompt": "AnySearch API key (optional)",
                "url": "https://www.anysearch.com/console/api-keys",
            }],
        }


class ExaAnySearchProvider(WebSearchProvider):
    """Exa first, AnySearch on any Exa failure — a real-time search fallback chain.

    Exa's free tier is ~$10/month of credits; when it drains (HTTP 402/429) or
    errors, the same call is served by AnySearch (1,000 requests/day free)
    instead of the heavily throttled keyless ring. Every call retries Exa, so
    quota resets need no manual switch-back.
    """

    def __init__(self) -> None:
        self._exa = None
        self._any = AnySearchProvider()

    def _exa_provider(self):
        """Lazily import the built-in Exa provider so its behaviour stays identical."""
        if self._exa is None:
            try:
                from plugins.web.exa.provider import ExaWebSearchProvider

                self._exa = ExaWebSearchProvider()
            except Exception as exc:  # noqa: BLE001 — degrade to AnySearch only
                logger.warning("Exa provider unavailable (%s); chain runs AnySearch only", exc)
                self._exa = False  # sentinel: import already failed once
        return self._exa or None

    @property
    def name(self) -> str:
        return "exa-anysearch"

    @property
    def display_name(self) -> str:
        return "Exa → AnySearch"

    def is_available(self) -> bool:
        return True  # AnySearch's anonymous tier always answers

    def is_keyless_available(self) -> bool:
        return False

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        exa = self._exa_provider()
        reason = "EXA_API_KEY not set or Exa provider unavailable"
        if exa is not None and exa.is_available():
            try:
                resp = exa.search(query, limit)
                if resp and resp.get("success") and (resp.get("data") or {}).get("web"):
                    return resp
                reason = str((resp or {}).get("error") or "Exa returned no results")
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}"

        logger.warning("Exa unavailable (%s) — falling back to AnySearch for '%s'", reason, query)
        out = self._any.search(query, limit)
        if out.get("success"):
            out.setdefault("data", {}).update(
                rescued_from="exa",
                backend_error=(
                    f"Backend 'exa' failed this call ({reason[:300]}); result served by "
                    f"AnySearch. The next call will try Exa again."
                ),
            )
        return out

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        exa = self._exa_provider()
        if exa is not None and exa.is_available():
            try:
                docs = exa.extract(urls)
                if docs and not all(d.get("error") for d in docs):
                    return docs
                reason = "Exa returned no content"
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}"
        else:
            reason = "EXA_API_KEY not set or Exa provider unavailable"

        logger.warning("Exa extract unavailable (%s) — falling back to AnySearch", reason)
        return _mark_rescued(self._any.extract(urls), "exa", reason)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Exa → AnySearch",
            "badge": "recommended · Exa first, AnySearch fallback",
            "tag": (
                "Exa semantic search with an automatic AnySearch fallback: if Exa's "
                "credit quota drains or errors, the same call is served by AnySearch "
                "(1,000 requests/day free) instead of the throttled keyless ring."
            ),
            "env_vars": [{
                "key": "ANYSEARCH_API_KEY",
                "prompt": "AnySearch API key (fallback backend)",
                "url": "https://www.anysearch.com/console/api-keys",
            }],
        }


class FirecrawlAnySearchProvider(WebSearchProvider):
    """Firecrawl first, AnySearch on any Firecrawl failure — the extract chain.

    Firecrawl is the stronger scraper (it clears anti-bot walls AnySearch cannot,
    e.g. npmjs / WeChat / z-lib), so it stays first. In a batch replay of 20 URLs
    that had previously failed on Firecrawl, failures were dominated by
    backend-level causes (unconfigured, 60s scrape timeouts) rather than hard
    pages, and AnySearch recovered 65% of them. Search is intentionally
    unsupported: keep this provider for ``web.extract_backend`` only.
    """

    def __init__(self) -> None:
        self._fc = None
        self._any = AnySearchProvider()

    def _fc_provider(self):
        """Lazily import the built-in Firecrawl provider (its extract is async)."""
        if self._fc is None:
            try:
                from plugins.web.firecrawl.provider import FirecrawlWebSearchProvider

                self._fc = FirecrawlWebSearchProvider()
            except Exception as exc:  # noqa: BLE001 — degrade to AnySearch only
                logger.warning("Firecrawl provider unavailable (%s); chain runs AnySearch only", exc)
                self._fc = False
        return self._fc or None

    @property
    def name(self) -> str:
        return "firecrawl-anysearch"

    @property
    def display_name(self) -> str:
        return "Firecrawl → AnySearch"

    def is_available(self) -> bool:
        return True

    def is_keyless_available(self) -> bool:
        return False

    def supports_search(self) -> bool:
        return False  # extract-only chain; use exa-anysearch for search

    def supports_extract(self) -> bool:
        return True

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        fc = self._fc_provider()
        reason = "FIRECRAWL_API_KEY not set or Firecrawl provider unavailable"
        if fc is not None and fc.is_available():
            try:
                docs = fc.extract(urls, **kwargs)
                if inspect.isawaitable(docs):  # built-in Firecrawl extract is async
                    docs = await docs
                if docs and not all(d.get("error") for d in docs):
                    return docs
                reason = "Firecrawl returned no content for any URL"
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}"

        logger.warning("Firecrawl extract unavailable (%s) — falling back to AnySearch", reason)
        return _mark_rescued(self._any.extract(urls), "firecrawl", reason)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Firecrawl → AnySearch",
            "badge": "recommended · Firecrawl first, AnySearch fallback",
            "tag": (
                "Firecrawl extraction with an automatic AnySearch fallback: if Firecrawl "
                "is unconfigured, rate-limited or times out, the same URLs are fetched by "
                "AnySearch (1,000 requests/day free) instead of failing outright."
            ),
            "env_vars": [{
                "key": "ANYSEARCH_API_KEY",
                "prompt": "AnySearch API key (fallback backend)",
                "url": "https://www.anysearch.com/console/api-keys",
            }],
        }


def _mark_rescued(docs: List[Dict[str, Any]], primary: str, reason: str) -> List[Dict[str, Any]]:
    """Annotate a fallback-served batch with the same fields core rescue uses."""
    for d in docs:
        meta = None if d.get("error") else d.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["rescued_from"] = primary
            meta["backend_error"] = (reason or "")[:300]
    return docs
