"""Offline vertical search and CLI contracts."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import httpx

# Support direct execution as well as unittest discovery, from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from client import AnySearchClient, AnySearchError


class VerticalTests(unittest.TestCase):
    def setUp(self):
        self.client = AnySearchClient(lambda: 'test-key')

    def test_search_options_and_legacy_payload(self):
        with patch('httpx.post', return_value=httpx.Response(200, json={'code': 0, 'data': {'results': []}})) as post:
            self.client.search('query', 3, tag='code.doc', params={'library': 'golang'}, zone='intl', language='en')
            self.assertEqual(post.call_args.kwargs['json'], {
                'query': 'query', 'max_results': 3, 'format': 'markdown', 'tag': 'code.doc',
                'params': {'library': 'golang'}, 'zone': 'intl', 'language': 'en'})
            self.client.search('query', 3)
            self.assertEqual(post.call_args.kwargs['json'], {'query': 'query', 'max_results': 3, 'format': 'markdown'})

    def test_invalid_options_do_not_request(self):
        with patch('httpx.post') as post:
            for opts in ({'tag': 'code'}, {'params': []}, {'params': {'x': float('nan')}},
                         {'zone': 'other'}, {'language': ''}):
                with self.subTest(opts=opts), self.assertRaises(ValueError):
                    self.client.search('query', **opts)
            post.assert_not_called()

    def test_domain_query_and_definitions(self):
        domains = [{'domain': 'code', 'sub_domains': [{'sub_domain': 'code.doc',
                    'params': {'library': {'required': True, 'description': 'Library name'}}}]}]
        with patch('httpx.get', return_value=httpx.Response(200, json={'code': 0, 'data': {'domains': domains}})) as get:
            self.assertEqual(self.client.sub_domains(['code', 'finance'])['data']['domains'], domains)
            self.assertEqual(get.call_args.kwargs['params'], [('domain', 'code'), ('domain', 'finance')])
            self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer test-key')
            self.assertNotIn('json', get.call_args.kwargs)

    def test_domain_response_errors_and_valid_empty_list(self):
        with patch('httpx.get') as get:
            get.return_value = httpx.Response(200, json={'code': 0, 'data': {'domains': []}})
            self.assertEqual(self.client.sub_domains(['code'])['data']['domains'], [])
            for data in ({}, {'domains': {}}, {'domains': [None]},
                         {'domains': [{'domain': 'code', 'sub_domains': [None]}]}):
                get.return_value = httpx.Response(200, json={'code': 0, 'data': data})
                with self.subTest(data=data), self.assertRaises(AnySearchError):
                    self.client.sub_domains(['code'])
            get.return_value = httpx.Response(429, json={'message': 'secret'})
            with self.assertRaisesRegex(AnySearchError, 'HTTP 429'):
                self.client.sub_domains(['code'])

    def test_invalid_domains_do_not_request(self):
        with patch('httpx.get') as get:
            for domains in ([], [''], ['code,finance'], 'code'):
                with self.subTest(domains=domains), self.assertRaises(ValueError):
                    self.client.sub_domains(domains)
            get.assert_not_called()

    def test_domains_are_normalized_before_deduplication(self):
        with patch('httpx.get', return_value=httpx.Response(
                200, json={'code': 0, 'data': {'domains': []}})) as get:
            self.client.sub_domains(['code', ' code ', 'finance', 'code'])
            self.assertEqual(get.call_args.kwargs['params'], [('domain', 'code'), ('domain', 'finance')])

    def test_unexpected_value_error_is_sanitized(self):
        parser = argparse.ArgumentParser()
        cli.setup_parser(parser)
        args = parser.parse_args(['search', 'query'])
        output = io.StringIO()
        with patch.object(cli, '_key', side_effect=ValueError('secret-marker')), contextlib.redirect_stdout(output):
            status = cli.handle_command(args)
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue()), {
            'success': False, 'error': 'Unexpected AnySearch command failure'})

    def run_cli(self, argv):
        parser = argparse.ArgumentParser()
        cli.setup_parser(parser)
        args = parser.parse_args(argv)
        output = io.StringIO()
        with patch.object(cli, '_key', return_value=''), contextlib.redirect_stdout(output):
            status = cli.handle_command(args)
        return status, json.loads(output.getvalue())

    def test_cli_search_and_domains(self):
        with patch('httpx.post', return_value=httpx.Response(200, json={'code': 0, 'data': {'results': []}})) as post:
            status, result = self.run_cli(['search', 'query', '--tag', 'code.doc', '--params', '{"library":"golang"}'])
            self.assertEqual(status, 0)
            self.assertTrue(result['success'])
            self.assertEqual(post.call_args.kwargs['json']['params'], {'library': 'golang'})
        with patch('httpx.get', return_value=httpx.Response(200, json={'code': 0, 'data': {'domains': []}})) as get:
            self.assertEqual(self.run_cli(['domains', '--domain', 'code', '--domain', 'finance'])[0], 0)
            self.assertEqual(get.call_args.kwargs['params'], [('domain', 'code'), ('domain', 'finance')])

    def test_cli_errors(self):
        with patch('httpx.post', return_value=httpx.Response(401, json={'message': 'secret'})):
            status, result = self.run_cli(['search', 'query'])
            self.assertEqual(status, 1)
            self.assertFalse(result['success'])
            self.assertNotIn('secret', result['error'])
        for argv in (['search', 'q', '--params', '[]'], ['search', 'q', '--limit', '11'],
                     ['domains'], ['search', 'q', '--params', '{"x": NaN}']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.run_cli(argv)
            self.assertEqual(error.exception.code, 2)

    def test_local_validation_error_has_no_capability_hint(self):
        with patch('httpx.post') as post:
            status, result = self.run_cli(['search', 'query', '--tag', 'codedoc'])
            self.assertEqual(status, 1)
            self.assertFalse(result['success'])
            self.assertIn('domain.sub_domain', result['error'])
            self.assertNotIn('hint', result)
            post.assert_not_called()

    def test_domains_400_has_no_search_hint(self):
        with patch('httpx.get', return_value=httpx.Response(400, json={})):
            status, result = self.run_cli(['domains', '--domain', 'code'])
            self.assertEqual(status, 1)
            self.assertFalse(result['success'])
            self.assertIn('HTTP 400', result['error'])
            self.assertNotIn('hint', result)

    def test_domains_502_remains_a_failure(self):
        with patch('httpx.get', return_value=httpx.Response(502, json={'message': 'secret'})):
            status, result = self.run_cli(['domains', '--domain', 'zzzz-notreal'])
            self.assertEqual(status, 1)
            self.assertFalse(result['success'])
            self.assertIn('HTTP 502', result['error'])
            self.assertNotIn('secret', result['error'])
            self.assertNotIn('data', result)

    def test_tagged_search_400_suggests_capability_discovery(self):
        for status_code in (400, 401, 429):
            with self.subTest(status_code=status_code), patch('httpx.post', return_value=httpx.Response(
                    status_code, json={'message': 'secret'})) as post:
                status, result = self.run_cli(['search', 'query', '--tag', 'code.doc'])
                self.assertEqual(status, 1)
                self.assertNotIn('secret', str(result))
                if status_code == 400:
                    self.assertIn('hermes anysearch domains --domain code', result['hint'])
                    self.assertIn('--params', result['hint'])
                else:
                    self.assertNotIn('hint', result)
                self.assertEqual(post.call_count, 1)
        with patch('httpx.post', return_value=httpx.Response(400, json={})):
            self.assertNotIn('hint', self.run_cli(['search', 'query'])[1])


if __name__ == '__main__':
    unittest.main()
