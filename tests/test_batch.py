"""Offline batch concurrency, context and CLI contracts."""
import argparse
import contextlib
from contextvars import ContextVar
import io
import json
import sys
import tempfile
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import httpx

# Support direct execution as well as unittest discovery, from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from batch import search_many
from client import AnySearchClient, AnySearchInputError


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.client = AnySearchClient(lambda: '')

    def test_concurrency_is_bounded_and_parallel(self):
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        active = peak = 0

        def post(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            barrier.wait()
            with lock:
                active -= 1
            # Keep the next pair from overlapping this pair's counters.
            barrier.wait()
            return httpx.Response(200, json={'code': 0, 'data': {'results': []}})

        with patch('httpx.post', side_effect=post):
            result = search_many(self.client, [{'query': str(i)} for i in range(6)], concurrency=2)
        self.assertTrue(result['success'])
        self.assertEqual(peak, 2)
        self.assertEqual(result['data']['succeeded'], 6)

    def test_out_of_order_completion_preserves_input_and_duplicates(self):
        second_done = threading.Event()

        def post(*args, **kwargs):
            if kwargs['json']['max_results'] == 1:
                if not second_done.wait(5):
                    raise RuntimeError('second job did not run')
            else:
                second_done.set()
            return httpx.Response(200, json={'code': 0, 'data': {'results': []}})

        with patch('httpx.post', side_effect=post):
            result = search_many(self.client, [{'query': 'same', 'limit': 1}, {'query': 'same', 'limit': 2}])
        self.assertTrue(result['success'])
        self.assertEqual([r['index'] for r in result['data']['results']], [0, 1])
        self.assertEqual([r['query'] for r in result['data']['results']], ['same', 'same'])

    def test_partial_failure_timeout_and_redaction(self):
        def post(*args, **kwargs):
            q = kwargs['json']['query']
            if q == 'timeout':
                raise httpx.ReadTimeout('secret')
            if q == 'unexpected':
                raise ValueError('secret')
            if q == 'denied':
                return httpx.Response(401, json={'message': 'secret'})
            return httpx.Response(200, json={'code': 0, 'data': {'results': []}})
        with patch('httpx.post', side_effect=post) as request:
            result = search_many(self.client, [{'query': q} for q in ['ok', 'denied', 'timeout', 'unexpected']])
        self.assertFalse(result['success'])
        self.assertEqual(result['data']['failed'], 3)
        self.assertEqual(result['data']['succeeded'], 1)
        self.assertNotIn('secret', json.dumps(result))
        self.assertIn('timeout', result['data']['results'][2]['error'])
        self.assertEqual(request.call_count, 4)

    def test_credentials_keep_caller_context_per_batch(self):
        key = ContextVar('batch_test_key')
        client = AnySearchClient(key.get)
        with patch('httpx.post', return_value=httpx.Response(200, json={'code': 0, 'data': {'results': []}})) as post:
            for credential in ('profile-a', 'profile-b', 'profile-a'):
                token = key.set(credential)
                try:
                    post.reset_mock()
                    result = search_many(client, [{'query': 'one'}, {'query': 'two'}])
                    self.assertTrue(result['success'])
                    self.assertEqual([call.kwargs['headers']['Authorization'] for call in post.call_args_list],
                                     ['Bearer ' + credential] * 2)
                finally:
                    key.reset(token)

    def test_rejects_entire_invalid_batch_before_requests(self):
        invalid = [[], {}, [{'query': 'x'}] * 21, [{'query': 'ok'}, {'query': ''}],
                   [{'query': 'x', 'unknown': 1}], [{'query': 'x', 'limit': True}],
                   [{'query': 'x', 'limit': 11}], [{'query': 'x', 'tag': 'bad'}],
                   [{'query': 'x', 'params': {'a': float('nan')}}], [None]]
        with patch('httpx.post') as post:
            for queries in invalid:
                with self.subTest(queries=queries), self.assertRaises(AnySearchInputError):
                    search_many(self.client, queries)
            for concurrency in (0, 5, True, '2'):
                with self.subTest(concurrency=concurrency), self.assertRaises(AnySearchInputError):
                    search_many(self.client, [{'query': 'x'}], concurrency=concurrency)
            post.assert_not_called()

    def run_cli(self, argv, stdin=''):
        parser = argparse.ArgumentParser()
        cli.setup_parser(parser)
        args = parser.parse_args(argv)
        output = io.StringIO()
        with patch.object(cli, '_key', return_value=''), patch('sys.stdin', io.StringIO(stdin)), contextlib.redirect_stdout(output):
            status = cli.handle_command(args)
        return status, json.loads(output.getvalue())

    def test_cli_file_and_stdin_with_per_query_options(self):
        queries = [{'query': 'docs', 'tag': 'code.doc', 'params': {'library': 'go'}, 'zone': 'intl', 'language': 'en'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'queries.json'
            path.write_text(json.dumps(queries), encoding='utf-8-sig')
            with patch('httpx.post', return_value=httpx.Response(200, json={'code': 0, 'data': {'results': []}})) as post:
                for source in (str(path), '-'):
                    status, result = self.run_cli(['batch', '--input', source, '--concurrency', '1'], json.dumps(queries))
                    self.assertEqual(status, 0)
                    self.assertEqual(result['data']['total'], 1)
                    self.assertEqual(post.call_args.kwargs['json']['params'], {'library': 'go'})

    def test_cli_distinguishes_file_and_encoding_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.json'
            invalid = Path(directory) / 'invalid.json'
            invalid.write_bytes(b'\xff')
            with patch('httpx.post') as post:
                for path, message in ((missing, 'file not found'), (invalid, 'UTF-8 encoded')):
                    with self.subTest(path=path):
                        status, result = self.run_cli(['batch', '--input', str(path)])
                        self.assertEqual(status, 1)
                        self.assertIn(message, result['error'])
                        self.assertNotIn(str(path), result['error'])
                with patch('builtins.open', side_effect=PermissionError('secret-path')):
                    status, result = self.run_cli(['batch', '--input', 'queries.json'])
                    self.assertEqual(status, 1)
                    self.assertIn('read permissions', result['error'])
                    self.assertNotIn('secret-path', result['error'])
                post.assert_not_called()

    def test_cli_failures_exit_nonzero(self):
        with patch('httpx.post', return_value=httpx.Response(429, json={'message': 'secret'})):
            status, result = self.run_cli(['batch', '--input', '-'], '[{"query":"q"}]')
            self.assertEqual(status, 1)
            self.assertEqual(result['data']['failed'], 1)
            self.assertNotIn('secret', json.dumps(result))
        for data in ('invalid secret', '[]', 'x' * 1_048_577):
            with self.subTest(data_length=len(data)), patch('httpx.post') as post:
                status, result = self.run_cli(['batch', '--input', '-'], data)
                self.assertEqual(status, 1)
                self.assertFalse(result['success'])
                self.assertNotIn('secret', json.dumps(result))
                post.assert_not_called()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.run_cli(['batch', '--input', '-', '--concurrency', '5'])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
