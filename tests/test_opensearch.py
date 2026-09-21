from __future__ import annotations

import unittest

from sentinel.telemetry.opensearch import OpenSearchAdapter


class OpenSearchAdapterTests(unittest.TestCase):
    def test_normalizes_otel_log(self) -> None:
        def transport(url, body, timeout):
            self.assertTrue(url.endswith("/otel-logs-*/_search"))
            self.assertEqual(body["query"]["bool"]["filter"][0]["term"]["traceId"], "abc")
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "@timestamp": "2026-09-21T12:00:00Z",
                                "body": "payment connection refused",
                                "severity": "ERROR",
                                "resource": {"service.name": "checkout"},
                                "traceId": "abc",
                                "spanId": "def",
                                "attributes": {"error.type": "ECONNREFUSED"},
                            }
                        }
                    ]
                }
            }

        adapter = OpenSearchAdapter("http://opensearch:9200", transport=transport)
        records = adapter.logs_for_trace("abc")
        self.assertEqual(records[0].service, "checkout")
        self.assertEqual(records[0].trace_id, "abc")
        self.assertIn("refused", records[0].body)


if __name__ == "__main__":
    unittest.main()
