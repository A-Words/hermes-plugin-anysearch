"""Argparse entry points for hermes anysearch."""
from __future__ import annotations

import argparse
import json

if __package__:
    from .client import AnySearchClient, AnySearchError
else:
    from client import AnySearchClient, AnySearchError


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


def _key() -> str:
    """Use the active Hermes profile's normal credential lookup, per request."""
    from agent.web_search_provider import get_provider_env
    return get_provider_env("ANYSEARCH_API_KEY")


def handle_command(args) -> int:
    client = AnySearchClient(_key)
    try:
        if args.anysearch_command == "domains":
            result = client.sub_domains(args.domain)
        elif args.anysearch_command == "search":
            result = client.search(args.query, args.limit, tag=args.tag, params=args.params,
                                   zone=args.zone, language=args.language)
        else:
            raise ValueError("unknown AnySearch command")
    except (AnySearchError, ValueError) as exc:
        result = {"success": False, "error": str(exc)}
        if (isinstance(exc, AnySearchError) and exc.status_code == 400
                and args.anysearch_command == "search" and args.tag):
            domain = args.tag.split(".", 1)[0]
            result["hint"] = (
                f"Run 'hermes anysearch domains --domain {domain}' to check the tag's "
                "required parameters, then supply them with --params."
            )
    except Exception:
        result = {"success": False, "error": "Unexpected AnySearch command failure"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1
