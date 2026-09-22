"""Offline regressions: python -m unittest discover -s tests -p test_client.py -v."""
import json
import unittest
from unittest.mock import patch

import httpx

from client import AnySearchClient, AnySearchError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = AnySearchClient(lambda: "")
        self.mock = patch("httpx.post").start()
        self.addCleanup(patch.stopall)

    def response(self, data, status=200, **kwargs):
        return httpx.Response(status, json=data, **kwargs)

    def ok(self, data):
        return self.response({"code": 0, "data": data})

    def mcp(self, text, **extra):
        return self.response({"jsonrpc": "2.0", "id": 1, "result": {
            "content": [{"type": "text", "text": text}], **extra}})

    def test_search_mapping_and_empty_results(self):
        self.mock.return_value = self.ok({"results": [{"title": "Title", "url": "https://example.com",
                                                     "content": "Markdown", "snippet": "short"}]})
        result = self.client.search("query", 99)
        self.assertEqual(result["data"]["web"], [{"title": "Title", "url": "https://example.com",
                                                 "description": "Markdown", "position": 1}])
        self.assertEqual(self.mock.call_args.kwargs["json"]["max_results"], 10)
        self.mock.return_value = self.ok({"results": []})
        self.assertEqual(self.client.search("query"), {"success": True, "data": {"web": []}})

    def test_invalid_search_responses_fail(self):
        for body in ({"code": -1, "message": "secret"}, {}, [],
                     {"code": 0, "data": {}}, {"code": 0, "data": {"results": [None]}},
                     {"code": 0, "data": {"results": [{"url": "x", "content": {}}]}}):
            with self.subTest(body=body):
                self.mock.return_value = self.response(body)
                with self.assertRaises(AnySearchError):
                    self.client.search("query")

    def test_credentials_resolved_each_request(self):
        keys = iter(("key-a", "key-b", "key-a", ""))
        self.client = AnySearchClient(lambda: next(keys))
        self.mock.return_value = self.ok({"results": []})
        for _ in range(4):
            self.client.search("query")
        self.assertEqual([c.kwargs["headers"].get("Authorization") for c in self.mock.call_args_list],
                         ["Bearer key-a", "Bearer key-b", "Bearer key-a", None])

    def test_terminal_http_errors_do_not_fallback_or_expose_body(self):
        request_id = "5a3f8c27-1e64-4b90-a752-6d9e2f41c083"
        for status in (400, 401, 402, 403, 415, 422, 429):
            with self.subTest(status=status):
                self.mock.reset_mock()
                self.mock.return_value = self.response({"message": "password=secret api_key=secret",
                                                       "request_id": request_id}, status)
                with self.assertLogs("client", level="WARNING") as logs:
                    result = self.client.extract(["https://example.com"])[0]
                self.assertEqual(self.mock.call_count, 1)
                self.assertIn(str(status), result["error"])
                self.assertIn(request_id, result["error"])
                self.assertNotIn("secret", str(result) + str(logs.output))

    def test_untrusted_request_id_not_echoed(self):
        self.mock.return_value = self.response({"request_id": "api_key=secret"}, 402)
        self.assertNotIn("secret", str(self.client.extract(["https://example.com"])))

    def test_extract_rest_success(self):
        self.mock.return_value = self.ok({"title": "Page", "content": "Body"})
        result = self.client.extract(["https://example.com"])[0]
        self.assertEqual(result["raw_content"], "Body")
        self.assertNotIn("error", result)
        self.assertEqual(self.mock.call_count, 1)

    def test_recoverable_failures_fallback_to_mcp(self):
        failures = [self.response({}, 503), self.response({}, 404),
                    self.ok({"title": "", "content": " "}),
                    httpx.Response(200, text="not json"),
                    httpx.ReadTimeout("secret transport text")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.mock.reset_mock()
                self.mock.side_effect = [failure, self.mcp(json.dumps({"title": "Page", "content": "Body"}))]
                self.assertEqual(self.client.extract_one("https://example.com"), ("Page", "Body"))
                self.assertEqual(self.mock.call_count, 2)

    def test_invalid_business_codes_allow_extract_fallback(self):
        for body in ({}, {"code": None}, {"code": "0"}, {"code": False}, {"code": 0.0}):
            with self.subTest(body=body):
                self.mock.reset_mock()
                self.mock.side_effect = [self.response(body), self.mcp("Recovered body")]
                self.assertEqual(self.client.extract_one("https://example.com"), ("", "Recovered body"))
                self.assertEqual(self.mock.call_count, 2)

    def test_nonzero_business_codes_do_not_fallback(self):
        for code in (-1, 1):
            with self.subTest(code=code):
                self.mock.reset_mock()
                self.mock.return_value = self.response({"code": code, "message": "secret"})
                with self.assertRaisesRegex(AnySearchError, "unsuccessful response"):
                    self.client.extract_one("https://example.com")
                self.assertEqual(self.mock.call_count, 1)

    def test_mcp_errors_are_not_documents(self):
        failures = [self.mcp("secret", isError=True),
                    self.response({"id": 1, "error": {"message": "secret"}}),
                    self.mcp('{"error":"secret"}'), self.mcp('{"code":-1,"message":"secret"}'),
                    self.mcp('{"url":"https://example.com"}'), self.mcp(" ")]
        for failure in failures:
            with self.subTest(failure=failure.text):
                self.mock.side_effect = [self.response({}, 503), failure]
                result = self.client.extract(["https://example.com"])[0]
                self.assertIn("error", result)
                self.assertEqual(result["content"], "")
                self.assertNotIn("secret", result["error"])

    def test_sse_skips_notifications_and_joins_data_lines(self):
        event = ('event: message\r\ndata: {"method":"notifications/progress"}\r\n\r\n'
                 'event: message\r\ndata: {"id":1,\r\ndata: "result":{"content":'
                 '[{"type":"text","text":"Body"}]}}\r\n\r\n')
        self.mock.side_effect = [self.response({}, 503), httpx.Response(200, text=event,
                                headers={"Content-Type": "text/event-stream"})]
        self.assertEqual(self.client.extract_one("https://example.com"), ("", "Body"))

    def test_each_url_has_independent_outcome(self):
        self.mock.side_effect = [self.response({}, 401), self.ok({"title": "", "content": "Body"})]
        result = self.client.extract(["https://a.example", "https://b.example"])
        self.assertIn("error", result[0])
        self.assertEqual(result[1]["content"], "Body")

    def test_unexpected_failure_is_sanitized_without_fallback(self):
        self.mock.side_effect = RuntimeError("password=secret")
        result = self.client.extract(["https://example.com"])[0]
        self.assertEqual(result["error"], "Unexpected AnySearch extract failure")
        self.assertEqual(self.mock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
