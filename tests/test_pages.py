import unittest

from groks_secret.x_api import _merge_pages, _pages


class PagesTests(unittest.TestCase):
    def test_consumes_generator_pages(self) -> None:
        def gen():
            yield {"data": [{"id": "1"}], "meta": {"next_token": "n"}}
            yield {"data": [{"id": "2"}], "meta": {}}

        merged = _merge_pages(_pages(gen()))
        ids = [row["id"] for row in merged["data"]]
        self.assertEqual(ids, ["1", "2"])

    def test_preserves_message_request_flag(self) -> None:
        merged = _merge_pages(
            [{"data": [], "meta": {"has_message_requests": True, "result_count": 0}}]
        )
        self.assertTrue(merged["meta"]["has_message_requests"])
        self.assertEqual(merged["meta"]["result_count"], 0)

    def test_stops_after_max_pages(self) -> None:
        def gen():
            yield {"data": [{"id": "1"}]}
            yield {"data": [{"id": "2"}]}

        pages = _pages(gen(), max_pages=1)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["data"][0]["id"], "1")
