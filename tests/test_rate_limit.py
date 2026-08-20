from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from requests import Response
from requests.exceptions import HTTPError

from groks_secret.bot import activity_to_page
from groks_secret.x_api import RateLimited, XChatClient, http_status, retry_after_seconds


def _http_429(retry_after: str = "30") -> HTTPError:
    resp = Response()
    resp.status_code = 429
    resp.headers["Retry-After"] = retry_after
    err = HTTPError("429 Client Error: Too Many Requests")
    err.response = resp
    return err


class RateLimitHelpersTests(unittest.TestCase):
    def test_status_and_retry_after(self) -> None:
        err = _http_429("45")
        self.assertEqual(http_status(err), 429)
        self.assertEqual(retry_after_seconds(err), 45.0)


class GetEventsTests(unittest.TestCase):
    def test_429_does_not_retry_over_http(self) -> None:
        api = XChatClient("token")
        api.client = MagicMock()
        api.client.chat.get_conversation_events.side_effect = _http_429()
        api._request = MagicMock()
        with self.assertRaises(RateLimited) as caught:
            api.get_events("1-2")
        self.assertGreaterEqual(caught.exception.retry_after, 1)
        api._request.assert_not_called()


class ActivityPageTests(unittest.TestCase):
    def test_maps_stream_payload(self) -> None:
        page = activity_to_page(
            {
                "conversation_id": "1:2",
                "sender_id": "1",
                "encoded_event": "abc",
                "id": "evt1",
                "created_at_msec": 1_700_000_000_000,
                "conversation_key_change_event": "keyblob",
            }
        )
        assert page is not None
        self.assertEqual(page["data"][0]["id"], "evt1")
        self.assertEqual(page["data"][0]["conversation_id"], "1-2")
        self.assertEqual(page["meta"]["conversation_key_events"], ["keyblob"])

    def test_ignores_join_without_ciphertext(self) -> None:
        self.assertIsNone(activity_to_page({"conversation_id": "1-2", "sender_id": "1"}))


if __name__ == "__main__":
    unittest.main()
