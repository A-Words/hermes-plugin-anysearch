"""Argparse entry points for hermes anysearch."""
from __future__ import annotations

import argparse
import json
import sys

if __package__:
    from .batch import search_many, DEFAULT_CONCURRENCY, MAX_CONCURRENCY
    from .client import AnySearchClient, AnySearchError, AnySearchInputError
else:
    from batch import search_many, DEFAULT_CONCURRENCY, MAX_CONCURRENCY
    from client import AnySearchClient, AnySearchError, AnySearchInputError


def _json_object(value: str) -> dict:
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError
        json.dumps(result, allow_nan=False)
        return result
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("params must be a valid JSON object") from None


def setup_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="anysearch_command", required=True)
    domains = commands.add_parser("domains", help="Discover sub-domains and parameter definitions")
    domains.add_argument("--domain", action="append", required=True,
                         help="Domain name; repeat for multiple domains")
    search = commands.add_parser("search", help="Search AnySearch with optional domain routing")
    search.add_argument("query", help="Search query")
    search.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    search.add_argument("--tag", help="Capability tag, e.g. code.doc")
    search.add_argument("--params", type=_json_object, help="Capability parameters as a JSON object")
    search.add_argument("--zone", choices=("cn", "intl"))
    search.add_argument("--language", help="Preferred language, e.g. en or zh-CN")
    batch = commands.add_parser("batch", help="Run a JSON list of searches concurrently")
    batch.add_argument("--input", required=True, help="UTF-8 JSON file, or - for stdin")
    batch.add_argument("--concurrency", type=int, choices=range(1, MAX_CONCURRENCY + 1),
                       default=DEFAULT_CONCURRENCY)


def _read_batch(source: str):
    """Read bounded input; keep parse errors and file contents out of diagnostics."""
    max_chars = 1_048_576
    try:
        if source == "-":
            text = sys.stdin.read(max_chars + 1)
        else:
            with open(source, encoding="utf-8-sig") as stream:
                text = stream.read(max_chars + 1)
        if len(text) > max_chars:
            raise AnySearchInputError("batch input exceeds 1,048,576 characters")
        return json.loads(text.lstrip("\ufeff"))
    except (OSError, UnicodeError):
        raise AnySearchInputError("Could not read batch input as UTF-8 JSON") from None
    except json.JSONDecodeError:
        raise AnySearchInputError("batch input must be valid JSON") from None


def _key() -> str:
    """Use the active Hermes profile's normal credential lookup, per request."""
    from agent.web_search_provider import get_provider_env
    return get_provider_env("ANYSEARCH_API_KEY")


def _capability_hint(args: argparse.Namespace, exc: Exception) -> str | None:
    """Suggest discovery only for a tagged search rejected with HTTP 400."""
    status_code = getattr(exc, "status_code", None)
    command = getattr(args, "anysearch_command", None)
    tag = getattr(args, "tag", None)
    if not isinstance(exc, AnySearchError) or status_code != 400 or command != "search":
        return None
    if not isinstance(tag, str) or not tag:
        return None
    domain = tag.split(".", 1)[0]
    return (
        f"Run 'hermes anysearch domains --domain {domain}' to check the tag's "
        "required parameters, then supply them with --params."
    )


def handle_command(args) -> int:
    client = AnySearchClient(_key)
    try:
        if args.anysearch_command == "domains":
            result = client.sub_domains(args.domain)
        elif args.anysearch_command == "search":
            result = client.search(args.query, args.limit, tag=args.tag, params=args.params,
                                   zone=args.zone, language=args.language)
        elif args.anysearch_command == "batch":
            result = search_many(client, _read_batch(args.input), concurrency=args.concurrency)
        else:
            raise AnySearchInputError("unknown AnySearch command")
    except (AnySearchError, AnySearchInputError) as exc:
        result = {"success": False, "error": str(exc)}
        hint = _capability_hint(args, exc)
        if hint is not None:
            result["hint"] = hint
    except Exception:
        result = {"success": False, "error": "Unexpected AnySearch command failure"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1
