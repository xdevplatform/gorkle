from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from requests import Response
from requests.exceptions import HTTPError

from gorkle.bot import activity_to_page
from gorkle.x_api import RateLimited, XChatClient, http_status, retry_after_seconds


def _http_429(retry_after: str = "30") -> HTTPError:
    resp = Response()
    resp.status_code = 429
    resp.headers["Retry-After"] = retry_after
    err = HTTPError("429 Client Error: Too Many Requests")
    err.response = resp
    return err


class PublicKeyCacheTests(unittest.TestCase):
    def test_second_lookup_does_not_hit_api(self) -> None:
        api = XChatClient("token")
        api.client = MagicMock()
        api.client.users.get_public_key.return_value = MagicMock(
            data=[{"public_key": "pk", "public_key_version": "1"}]
        )
        first = api.get_public_keys("99")
        second = api.get_public_keys("99")
        self.assertEqual(first, second)
        self.assertEqual(api.client.users.get_public_key.call_count, 1)


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


class BackfillFallbackTests(unittest.TestCase):
    def test_detects_backfill_rejection(self) -> None:
        from xdk.streaming import StreamError, StreamErrorType

        from gorkle.x_api import _rejects_backfill

        err = StreamError(
            "Client error (400): Bad request",
            StreamErrorType.CLIENT_ERROR,
            status_code=400,
            response_body="Stream is not authorized to use backfill_minutes parameter",
        )
        self.assertTrue(_rejects_backfill(err))
        self.assertFalse(_rejects_backfill(RuntimeError("connection reset")))
        self.assertFalse(
            _rejects_backfill(
                StreamError("Client error (401): Unauthorized", StreamErrorType.AUTHENTICATION_ERROR, status_code=401)
            )
        )

    def test_stream_reconnects_without_backfill(self) -> None:
        from unittest.mock import patch

        from xdk.streaming import StreamError, StreamErrorType

        calls: list[dict] = []

        def fake_activity(**kwargs):
            calls.append(kwargs)
            if "backfill_minutes" in kwargs:
                raise StreamError("Client error (400): Bad request", StreamErrorType.CLIENT_ERROR, status_code=400)
            yield {"data": {"event_type": "chat.received"}}

        fake_client = MagicMock()
        fake_client.stream.activity.side_effect = fake_activity
        with patch("xdk.Client", return_value=fake_client):
            client = XChatClient.__new__(XChatClient)
            gen = client.iter_activity_stream("bearer", backfill_minutes=5)
            first = next(gen)
        self.assertEqual(first, {"data": {"event_type": "chat.received"}})
        self.assertIn("backfill_minutes", calls[0])
        self.assertNotIn("backfill_minutes", calls[1])
