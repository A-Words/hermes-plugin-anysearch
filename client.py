"""AnySearch transport and response handling, independent of Hermes."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List
from uuid import UUID

logger = logging.getLogger(__name__)

ANYSEARCH_API_BASE = "https://api.anysearch.com"
_SEARCH_TIMEOUT = 30.0
_EXTRACT_TIMEOUT = 60.0
# `content` under format=markdown is a structured abstract (title/url + excerpt);
# longer and better-shape than `snippet` (measured 222-345 chars vs 100).
_SEARCH_FORMAT = "markdown"


class AnySearchInputError(ValueError):
    """A validated, safe-to-display input error."""


class AnySearchError(RuntimeError):
    """Safe diagnostic: never include response bodies or transport exception text."""

    def __init__(self, message: str, *, fallback: bool = False, request_id=None,
                 status_code: int | None = None):
        self.fallback = fallback
        self.status_code = status_code
        self.request_id = None
        if isinstance(request_id, str):
            try:
                self.request_id = str(UUID(request_id))
            except ValueError:
                pass
        if self.request_id:
            message += f" (request_id={self.request_id})"
        super().__init__(message)


class AnySearchClient:
    """Thin REST client for AnySearch. No key required (anonymous tier)."""

    def __init__(self, key_supplier: Callable[[], str]):
        # Resolve per request, under the caller's active Hermes profile.
        self._key_supplier = key_supplier

    def _post(self, path: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        return self._request(path, timeout, payload=payload)

    def _request(self, path: str, timeout: float, *, payload=None, params=None) -> Dict[str, Any]:
        import httpx

        headers = {"Content-Type": "application/json"}
        key = self._key_supplier()
        if key:
            headers["Authorization"] = "Bearer " + key
        if path == "/mcp":
            headers["Accept"] = "application/json, text/event-stream"
        try:
            if payload is None:
                resp = httpx.get(ANYSEARCH_API_BASE + path, params=params, headers=headers,
                                 timeout=timeout, follow_redirects=True)
            else:
                resp = httpx.post(
                    ANYSEARCH_API_BASE + path, json=payload, headers=headers,
                    timeout=timeout, follow_redirects=True,
                )
        except httpx.RequestError as exc:
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network failure"
            raise AnySearchError(f"AnySearch {kind}", fallback=True) from None
        if not resp.is_success:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            request_id = body.get("request_id") if isinstance(body, dict) else None
            descriptions = {400: "invalid request", 401: "invalid credentials",
                            402: "quota exhausted", 403: "access forbidden",
                            429: "rate limited", 422: "extraction failed"}
            status = resp.status_code
            raise AnySearchError(
                f"AnySearch HTTP {status}: {descriptions.get(status, 'request failed')}",
                fallback=status in (404, 405, 408) or status >= 500,
                request_id=request_id,
                status_code=status,
            )
        try:
            text = resp.text.strip()
            if path == "/mcp" and "text/event-stream" in resp.headers.get("content-type", ""):
                # Ignore notifications; select the response to our tools/call.
                raw = None
                for event in text.replace("\r\n", "\n").split("\n\n"):
                    lines = [line[5:].lstrip(" ") for line in event.splitlines()
                             if line.startswith("data:")]
                    if not lines:
                        continue
                    candidate = json.loads("\n".join(lines))
                    if isinstance(candidate, dict) and candidate.get("id") == 1:
                        raw = candidate
                        break
            else:
                raw = json.loads(text)
        except ValueError:
            raise AnySearchError("AnySearch returned invalid JSON", fallback=True) from None
        if not isinstance(raw, dict):
            raise AnySearchError("AnySearch returned an invalid response", fallback=True)
        if path != "/mcp":
            if type(raw.get("code")) is not int:
                raise AnySearchError("AnySearch returned an invalid business code", fallback=True,
                                     request_id=raw.get("request_id"))
            if raw["code"] != 0:
                raise AnySearchError("AnySearch returned an unsuccessful response",
                                     request_id=raw.get("request_id"))
            if not isinstance(raw.get("data"), dict):
                raise AnySearchError("AnySearch response is missing data", fallback=True,
                                     request_id=raw.get("request_id"))
        return raw

    def sub_domains(self, domains: List[str]) -> Dict[str, Any]:
        """Return successful capability definitions; preserve HTTP failures as errors."""
        if (not isinstance(domains, list) or not domains
                or any(not isinstance(d, str) or not d.strip() or "," in d for d in domains)):
            raise AnySearchInputError("domains must be a non-empty list of individual domain names")
        raw = self._request("/v1/sub-domains", _SEARCH_TIMEOUT,
                            params=[("domain", d) for d in dict.fromkeys(d.strip() for d in domains)])
        entries = raw["data"].get("domains")
        if not isinstance(entries, list):
            raise AnySearchError("AnySearch returned invalid domain definitions")
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get("domain"), str)
                    or not isinstance(entry.get("sub_domains"), list)):
                raise AnySearchError("AnySearch returned invalid domain definitions")
            for sub in entry["sub_domains"]:
                if (not isinstance(sub, dict) or not isinstance(sub.get("sub_domain"), str)
                        or ("params" in sub and not isinstance(sub["params"], dict))):
                    raise AnySearchError("AnySearch returned invalid sub-domain definitions")
        return {"success": True, "data": {"domains": entries}}

    def search(self, query: str, limit: int = 5, *, tag: str | None = None,
               params: Dict[str, Any] | None = None, zone: str | None = None,
               language: str | None = None) -> Dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise AnySearchInputError("query must be a non-empty string")
        if tag is not None and (not isinstance(tag, str) or not re.fullmatch(r"[\w-]+\.[\w-]+", tag)):
            raise AnySearchInputError("tag must have the form domain.sub_domain")
        if params is not None and not isinstance(params, dict):
            raise AnySearchInputError("params must be a JSON object")
        if params is not None:
            try:
                json.dumps(params, allow_nan=False)
            except (ValueError, TypeError):
                raise AnySearchInputError("params must contain JSON-compatible values") from None
        if zone is not None and zone not in ("cn", "intl"):
            raise AnySearchInputError("zone must be cn or intl")
        if language is not None and (not isinstance(language, str) or not language.strip()):
            raise AnySearchInputError("language must be a non-empty string")
        payload = {"query": query, "max_results": max(1, min(int(limit or 5), 10)),
                   "format": _SEARCH_FORMAT}
        for name, value in (("tag", tag), ("params", params), ("zone", zone), ("language", language)):
            if value is not None:
                payload[name] = value
        raw = self._post(
            "/v1/search",
            payload,
            _SEARCH_TIMEOUT,
        )
        results = raw["data"].get("results")
        if not isinstance(results, list):
            raise AnySearchError("AnySearch response is missing results")
        hits = []
        for i, r in enumerate(results):
            if (not isinstance(r, dict) or not isinstance(r.get("url"), str)
                    or not r["url"].strip()
                    or any(r.get(k) is not None and not isinstance(r[k], str)
                           for k in ("title", "content", "snippet"))):
                raise AnySearchError("AnySearch returned an invalid search result")
            hits.append({
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                # markdown-mode `content` is the richer field; snippet is the fallback.
                "description": r.get("content") or r.get("snippet") or "",
                "position": i + 1,
            })
        return {"success": True, "data": {"web": hits}}

    def _extract_rest(self, url: str) -> tuple[str, str]:
        """Read a document from the official REST extract endpoint."""
        raw = self._post("/v1/extract", {"url": url}, _EXTRACT_TIMEOUT)
        return self._document(raw["data"])

    @staticmethod
    def _document(data: dict) -> tuple[str, str]:
        title, content = data.get("title", ""), data.get("content")
        if not isinstance(title, str) or not isinstance(content, str) or not content.strip():
            raise AnySearchError("AnySearch returned empty or invalid content", fallback=True)
        return title, content

    def _extract_mcp(self, url: str) -> tuple[str, str]:
        """MCP fallback. NOTE: ``result.content[].text`` is a JSON *string*."""
        data = self._post("/mcp", {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "extract", "arguments": {"url": url}},
        }, _EXTRACT_TIMEOUT)
        result = data.get("result")
        if (data.get("error") is not None or data.get("id") != 1
                or not isinstance(result, dict) or result.get("isError")):
            raise AnySearchError("AnySearch MCP extraction failed")
        blocks = result.get("content")
        if not isinstance(blocks, list):
            raise AnySearchError("AnySearch MCP returned invalid content")
        for item in blocks:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                inner = json.loads(text)
            except ValueError:
                return "", text
            # A JSON wrapper must contain a valid document, never error text.
            if not isinstance(inner, dict) or inner.get("error") or inner.get("isError"):
                raise AnySearchError("AnySearch MCP returned an invalid document")
            if "code" in inner:
                if type(inner["code"]) is not int or inner["code"] != 0:
                    raise AnySearchError("AnySearch MCP extraction failed")
                inner = inner.get("data")
                if not isinstance(inner, dict):
                    raise AnySearchError("AnySearch MCP returned an invalid document")
            return self._document(inner)
        raise AnySearchError("AnySearch MCP returned empty content")

    def extract_one(self, url: str) -> tuple[str, str]:
        try:
            return self._extract_rest(url)
        except AnySearchError as exc:
            if not exc.fallback:
                raise
            logger.info("AnySearch REST extract failed (%s); trying MCP", exc)
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
            except Exception as exc:  # Keep per-URL failures isolated.
                message = str(exc) if isinstance(exc, AnySearchError) else "Unexpected AnySearch extract failure"
                logger.warning("AnySearch extract failed: %s", message)
                docs.append({
                    "url": url, "title": "", "content": "", "raw_content": "",
                    "error": message, "metadata": {"sourceURL": url},
                })
        return docs
