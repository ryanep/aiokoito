"""Regression coverage for request efficiency, failure semantics and cancellation."""

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from aiokoito import KoitoApi, KoitoConnectionError, KoitoResponseError
import pytest
from test_client import VALID_SUMMARY, session_for


def test_fetch_uses_only_summary_and_playback_preserving_all_ranked_records():
    rankings = [{"rank": i + 1, "item": {"title": f"Song {i}"}} for i in range(5)]
    session = session_for(
        {
            "summary": {**VALID_SUMMARY, "top_tracks": rankings},
            "now-playing": {"currently_playing": False},
        }
    )
    data = asyncio.run(
        KoitoApi("https://koito.example", "key", session).async_fetch_data()
    )
    assert session.get.call_count == 2
    assert [record.item.title for record in data.summary.top_tracks] == [
        f"Song {i}" for i in range(5)
    ]


@pytest.mark.parametrize(
    "record",
    [
        None,
        "bad",
        {},
        {"item": "bad", "rank": 1},
        {"item": {}, "rank": True},
        {"item": {}, "rank": 0},
    ],
)
def test_malformed_summary_ranked_records_are_response_errors(record):
    session = session_for({"summary": {**VALID_SUMMARY, "top_tracks": [record]}})
    with pytest.raises(KoitoResponseError):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_summary()
        )


def test_period_is_a_caller_choice():
    api = KoitoApi("https://koito.example", "key", MagicMock())
    api.async_get_json = AsyncMock(return_value=VALID_SUMMARY)
    asyncio.run(api.async_get_summary(period="this_month"))
    api.async_get_json.assert_awaited_once_with("summary", {"period": "this_month"})


def test_invalid_json_content_type_is_a_response_error():
    session = MagicMock()
    session.get.side_effect = aiohttp.ContentTypeError(
        request_info=MagicMock(), history=(), status=200
    )
    with pytest.raises(KoitoResponseError):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_json("summary")
        )


@pytest.mark.parametrize("payload", [None, [], "not an object"])
def test_non_object_json_is_a_response_error(payload):
    session = session_for({"summary": payload})
    with pytest.raises(KoitoResponseError, match="JSON object"):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_json("summary")
        )


@pytest.mark.parametrize(
    "header, expected",
    [
        ("120", 120),
        ("0", 0),
        ("-1", None),
        ("junk", None),
        ("inf", None),
        (None, None),
        pytest.param("9" * 500, None, id="overflowing-delay"),
        pytest.param("9" * 5000, None, id="oversized-delay"),
    ],
)
def test_http_errors_preserve_status_and_valid_backoff(header, expected):
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=429,
        headers={} if header is None else {"Retry-After": header},
    )
    with pytest.raises(KoitoConnectionError) as caught:
        asyncio.run(
            KoitoApi("https://koito.example", "secret", session).async_get_json(
                "summary"
            )
        )
    assert caught.value.status == 429
    assert caught.value.retry_after == expected
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("date_format", ["standard", "obsolete", "past"])
def test_http_date_backoff(date_format):
    now = datetime(2026, 10, 10, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=90), usegmt=True)
    if date_format == "obsolete":
        header = "Sat Oct 10 00:01:30 2026"
    elif date_format == "past":
        header = format_datetime(now - timedelta(seconds=90), usegmt=True)
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=503,
        headers={"Retry-After": header},
    )
    with patch("aiokoito.client.datetime") as clock:
        clock.now.return_value = now
        with pytest.raises(KoitoConnectionError) as caught:
            asyncio.run(
                KoitoApi("https://koito.example", "key", session).async_get_json(
                    "summary"
                )
            )
    assert caught.value.retry_after == (0 if date_format == "past" else 90)


def test_concurrent_refresh_waits_for_both_endpoints():
    async def run():
        started = set()
        both = asyncio.Event()
        api = KoitoApi("https://koito.example", "key", MagicMock())

        async def get(path, params=None):
            started.add(path)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 0.5)
            return VALID_SUMMARY if path == "summary" else {"currently_playing": False}

        api.async_get_json = get
        data = await api.async_fetch_data(period="this_month")
        assert data.summary.plays == 0
        assert data.now_playing.currently_playing is False

    asyncio.run(run())


def test_playback_deadline_keeps_successful_summary_and_cleans_up():
    async def run():
        api = KoitoApi(
            "https://koito.example", "key", MagicMock(), refresh_timeout=0.05
        )
        cleaned = asyncio.Event()

        async def get(path, params=None):
            if path == "summary":
                return VALID_SUMMARY
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        api.async_get_json = get
        data = await api.async_fetch_data()
        assert data.summary.plays == 0
        assert data.now_playing is None
        assert cleaned.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["deadline", "cancel", "failure"])
def test_refresh_cancels_and_awaits_all_sibling_tasks(mode):
    async def run():
        api = KoitoApi(
            "https://koito.example", "key", MagicMock(), refresh_timeout=0.05
        )
        started = asyncio.Event()
        cleaned = set()

        async def get(path, params=None):
            try:
                if path == "summary" and mode == "failure":
                    await started.wait()
                    raise KoitoResponseError("bad summary")
                started.set()
                await asyncio.Event().wait()
            finally:
                cleaned.add(path)

        api.async_get_json = get
        task = asyncio.create_task(api.async_fetch_data())
        await started.wait()
        if mode == "cancel":
            task.cancel()
        error = {
            "deadline": KoitoConnectionError,
            "cancel": asyncio.CancelledError,
            "failure": KoitoResponseError,
        }[mode]
        with pytest.raises(error):
            await task
        assert cleaned == {"summary", "now-playing"}

    asyncio.run(run())


@pytest.mark.parametrize("status, header", [(429, None), (503, "90")])
def test_optional_playback_does_not_hide_server_backoff(status, header):
    api = KoitoApi("https://koito.example", "key", MagicMock())

    async def get(path, params=None):
        if path == "summary":
            return VALID_SUMMARY
        raise KoitoConnectionError(
            "backoff", status=status, retry_after=None if header is None else 90
        )

    api.async_get_json = get
    with pytest.raises(KoitoConnectionError):
        asyncio.run(api.async_fetch_data())
