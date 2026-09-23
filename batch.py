"""Bounded batch execution using the existing single-query client."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from typing import Any

if __package__:
    from .client import AnySearchClient, AnySearchError, AnySearchInputError
else:
    from client import AnySearchClient, AnySearchError, AnySearchInputError

MAX_BATCH_SIZE = 20
MAX_CONCURRENCY = 4
DEFAULT_CONCURRENCY = 3
_FIELDS = {'query', 'limit', 'tag', 'params', 'zone', 'language'}


def search_many(client: AnySearchClient, queries: list[dict[str, Any]], *,
                concurrency: int = DEFAULT_CONCURRENCY) -> dict[str, Any]:
    """Validate all input first, then return one outcome per query in input order."""
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise AnySearchInputError(f'concurrency must be between 1 and {MAX_CONCURRENCY}')
    if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_BATCH_SIZE:
        raise AnySearchInputError(f'queries must be a list containing 1 to {MAX_BATCH_SIZE} objects')
    prepared = []
    for index, query in enumerate(queries):
        if not isinstance(query, dict) or 'query' not in query or set(query) - _FIELDS:
            raise AnySearchInputError(f'queries[{index}] must contain query and only supported search fields')
        limit = query.get('limit', 5)
        if type(limit) is not int or not 1 <= limit <= 10:
            raise AnySearchInputError(f'queries[{index}].limit must be an integer between 1 and 10')
        try:
            client.build_search_payload(**query)
        except AnySearchInputError as exc:
            raise AnySearchInputError(f'queries[{index}]: {exc}') from None
        prepared.append(deepcopy(query))

    def run(index: int, query: dict[str, Any]) -> dict[str, Any]:
        try:
            outcome = client.search(**query)
        except (AnySearchError, AnySearchInputError) as exc:
            outcome = {'success': False, 'error': str(exc)}
        except Exception:
            outcome = {'success': False, 'error': 'Unexpected AnySearch search failure'}
        return {'index': index, 'query': query['query'], **outcome}

    # Each job gets its own context: scoped credentials remain available in workers.
    # Credentials are still resolved per request, never cached or put in the result.
    with ThreadPoolExecutor(max_workers=min(concurrency, len(prepared))) as executor:
        futures = [executor.submit(copy_context().run, run, index, query)
                   for index, query in enumerate(prepared)]
        results = [future.result() for future in futures]
    succeeded = sum(result['success'] is True for result in results)
    return {'success': succeeded == len(results), 'data': {
        'results': results, 'total': len(results), 'succeeded': succeeded,
        'failed': len(results) - succeeded,
    }}
