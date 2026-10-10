from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
from aiokoito import (
    KoitoApi,
    KoitoAuthenticationError,
    KoitoConnectionError,
    MusicItem,
    NowPlaying,
    SimpleArtist,
    resolve_image_url,
)
import pytest


def response(payload):
    result = MagicMock()
    result.json = AsyncMock(return_value=payload)
    result.raise_for_status = MagicMock()
    return result


def session_for(payloads):
    session = MagicMock()
    session.get = MagicMock()

    def get(url, **kwargs):
        endpoint = urlsplit(url).path.rsplit("/", 1)[-1]
        result = response(payloads[endpoint])
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=result)
        manager.__aexit__ = AsyncMock(return_value=False)
        return manager

    session.get.side_effect = get
    return session


VALID_SUMMARY = {
    "plays": 0,
    "minutes_listened": 0,
    "unique_tracks": 0,
    "unique_albums": 0,
    "unique_artists": 0,
    "top_artists": [],
    "top_albums": [],
    "top_tracks": [],
}


def test_get_json_uses_token_header_normalized_url_params_and_only_get():
    session = session_for({"listens": {"items": []}})
    api = KoitoApi("https://koito.example///", "secret-key", session)

    asyncio.run(api.async_get_json("/listens", {"limit": 10, "period": "all time"}))

    session.get.assert_called_once()
    (url,) = session.get.call_args.args
    assert url == (
        "https://koito.example/apis/web/v1/listens?"
        + urlencode({"limit": 10, "period": "all time"})
    )
    assert session.get.call_args.kwargs["headers"] == {
        "Authorization": "Token secret-key"
    }
    assert "params" not in session.get.call_args.kwargs
    assert session.get.call_args.kwargs["timeout"].total == 10
    session.post.assert_not_called()
    session.put.assert_not_called()
    session.patch.assert_not_called()
    session.delete.assert_not_called()


def test_fetch_data_reuses_summary_rankings_without_extra_requests():
    artist = {
        "item": {
            "id": 1,
            "name": "Artist",
            "image": {"medium": "/artist.jpg"},
            "listen_count": 10,
        },
        "rank": 1,
    }
    album = {
        "item": {
            "id": 2,
            "title": "Album",
            "artists": [{"id": 1, "name": "Artist"}],
            "image": {"medium": "/album.jpg"},
            "listen_count": 10,
        },
        "rank": 1,
    }
    track = {
        "item": {
            "id": 3,
            "title": "Song",
            "artists": [{"id": 1, "name": "Artist"}],
            "image": {"medium": "/track.jpg"},
            "listen_count": 10,
        },
        "rank": 1,
    }
    summary = {
        "plays": 42,
        "minutes_listened": 120,
        "unique_tracks": 9,
        "unique_albums": 4,
        "unique_artists": 3,
        "top_artists": [artist, {"item": {"name": "Other Artist"}, "rank": 2}],
        "top_albums": [album, {"item": {"title": "Other Album"}, "rank": 2}],
        "top_tracks": [track, {"item": {"title": "Other Song"}, "rank": 2}],
    }
    payloads = {
        "summary": summary,
        "artists": {"items": [artist]},
        "albums": {"items": [album]},
        "tracks": {"items": [track]},
        "now-playing": {"currently_playing": False, "track": {}},
    }
    session = session_for(payloads)

    result = asyncio.run(
        KoitoApi("https://koito.example", "key", session).async_fetch_data()
    )

    assert result.summary.plays == 42
    assert result.summary.top_artists[0].item.name == "Artist"
    assert result.summary.top_albums[0].item.title == "Album"
    assert result.summary.top_tracks[0].item.title == "Song"
    assert result.summary.top_albums[0].item.artists == (SimpleArtist("Artist", 1),)
    assert len(result.summary.top_albums) == 2
    assert result.now_playing == NowPlaying(False, MusicItem())
    assert len(session.get.call_args_list) == 2
    assert all("/listens" not in call.args[0] for call in session.get.call_args_list)
    assert all(
        call.args[0].startswith("https://koito.example/apis/web/v1/")
        for call in session.get.call_args_list
    )
    assert all("/stats" not in call.args[0] for call in session.get.call_args_list)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "currently_playing": True,
                "track": {"title": "Song", "artists": ["Artist"]},
            },
            NowPlaying(
                True, MusicItem(title="Song", artists=(SimpleArtist("Artist"),))
            ),
        ),
        (
            {
                "currently_playing": False,
                "track": {"id": 0, "title": "", "artists": []},
            },
            NowPlaying(False, MusicItem(id=0, title="")),
        ),
    ],
)
def test_fetch_data_includes_valid_optional_now_playing(payload, expected):
    session = session_for(
        {
            "listens": {"items": []},
            "summary": VALID_SUMMARY,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
            "now-playing": payload,
        }
    )

    result = asyncio.run(
        KoitoApi("https://koito.example", "key", session).async_fetch_data()
    )

    assert result.now_playing == expected
    call = next(
        call for call in session.get.call_args_list if "now-playing" in call.args[0]
    )
    assert call.args[0].endswith("/now-playing")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"currently_playing": 1, "track": {}},
        {"currently_playing": True},
        {"currently_playing": True, "track": []},
    ],
)
def test_malformed_now_playing_is_optional_none(payload):
    session = session_for(
        {
            "listens": {"items": []},
            "summary": VALID_SUMMARY,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
            "now-playing": payload,
        }
    )

    assert (
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_fetch_data()
        ).now_playing
        is None
    )


def test_now_playing_http_failure_is_optional_none():
    session = session_for(
        {
            "listens": {"items": []},
            "summary": VALID_SUMMARY,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
        }
    )
    original_get = session.get.side_effect

    def get(url, **kwargs):
        if url.endswith("/now-playing"):
            raise aiohttp.ClientConnectionError("unavailable")
        return original_get(url, **kwargs)

    session.get.side_effect = get

    assert (
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_fetch_data()
        ).now_playing
        is None
    )


@pytest.mark.parametrize("status", [401, 403])
def test_now_playing_auth_errors_propagate(status):
    session = session_for(
        {
            "summary": VALID_SUMMARY,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
        }
    )
    original_get = session.get.side_effect

    def get(url, **kwargs):
        if url.endswith("/now-playing"):
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=status
            )
        return original_get(url, **kwargs)

    session.get.side_effect = get
    with pytest.raises(KoitoAuthenticationError):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_fetch_data()
        )


def test_unexpected_now_playing_errors_are_not_hidden():
    api = KoitoApi("https://koito.example", "key", MagicMock())
    api.async_get_json = AsyncMock(
        side_effect=[
            VALID_SUMMARY,
            RuntimeError("programming error"),
        ]
    )
    with pytest.raises(RuntimeError, match="programming error"):
        asyncio.run(api.async_fetch_data())


def test_fetch_data_always_uses_all_time_for_summary_and_rankings():
    session = session_for(
        {
            "listens": {"items": []},
            "summary": VALID_SUMMARY,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
            "now-playing": {"currently_playing": False},
        }
    )

    asyncio.run(KoitoApi("https://koito.example", "key", session).async_fetch_data())

    calls = {
        urlsplit(call.args[0]).path.rsplit("/", 1)[-1]: urlsplit(call.args[0]).query
        for call in session.get.call_args_list
    }
    assert parse_qs(calls["summary"]) == {"period": ["all_time"]}
    assert set(calls) == {"summary", "now-playing"}


@pytest.mark.parametrize(
    "field",
    [
        "plays",
        "minutes_listened",
        "unique_tracks",
        "unique_albums",
        "unique_artists",
        "top_artists",
        "top_albums",
        "top_tracks",
    ],
)
def test_fetch_data_rejects_missing_summary_fields(field):
    summary = {key: value for key, value in VALID_SUMMARY.items() if key != field}
    session = session_for(
        {
            "listens": {"items": []},
            "summary": summary,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
        }
    )

    with pytest.raises(ValueError, match="Invalid Koito summary response"):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_fetch_data()
        )


@pytest.mark.parametrize("value", [-1, 1.5, "1", True, None])
def test_fetch_data_rejects_malformed_summary_metrics(value):
    for field in (
        "plays",
        "minutes_listened",
        "unique_tracks",
        "unique_albums",
        "unique_artists",
    ):
        summary = {**VALID_SUMMARY, field: value}
        session = session_for(
            {
                "listens": {"items": []},
                "summary": summary,
                "artists": {"items": []},
                "albums": {"items": []},
                "tracks": {"items": []},
            }
        )
        with pytest.raises(ValueError, match="Invalid Koito summary response"):
            asyncio.run(
                KoitoApi("https://koito.example", "key", session).async_fetch_data()
            )


@pytest.mark.parametrize("field", ["top_artists", "top_albums", "top_tracks"])
@pytest.mark.parametrize("value", [None, {}, "items"])
def test_fetch_data_rejects_non_list_summary_rankings(field, value):
    summary = {**VALID_SUMMARY, field: value}
    session = session_for(
        {
            "listens": {"items": []},
            "summary": summary,
            "artists": {"items": []},
            "albums": {"items": []},
            "tracks": {"items": []},
        }
    )

    with pytest.raises(ValueError, match="Invalid Koito summary response"):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_fetch_data()
        )


@pytest.mark.parametrize("status", [401, 403, 500])
def test_get_json_raises_for_http_errors(status):
    class ResponseError(Exception):
        pass

    session = MagicMock()
    result = response({})
    result.raise_for_status.side_effect = ResponseError(status)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=result)
    manager.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = manager

    with pytest.raises(ResponseError):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_json("/summary")
        )


def test_get_json_propagates_invalid_json():
    session = session_for({"summary": {}})
    session.get.side_effect = None
    result = response(None)
    result.json.side_effect = ValueError("invalid JSON")
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=result)
    manager.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = manager

    with pytest.raises(ValueError, match="invalid JSON"):
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_json("/summary")
        )


@pytest.mark.parametrize(
    ("image_value", "size", "expected"),
    [
        (
            {"xs": "/image/id/64x64.webp", "large": "/image/id/640x640.webp"},
            "large",
            "https://koito.example/image/id/640x640.webp",
        ),
        (
            "/image/id/300x300.webp",
            "medium",
            "https://koito.example/image/id/300x300.webp",
        ),
        (
            {"large": "https://cdn.example/image.webp"},
            "large",
            "https://cdn.example/image.webp",
        ),
        (None, "large", None),
        ({}, "large", None),
    ],
)
def test_resolve_image_url_variants(image_value, size, expected):
    assert resolve_image_url(image_value, "https://koito.example/", size) == expected


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, KoitoAuthenticationError),
        (403, KoitoAuthenticationError),
        (429, KoitoConnectionError),
        (500, KoitoConnectionError),
    ],
)
def test_http_failures_use_sanitized_library_errors(status, error_type):
    session = MagicMock()
    session.get.side_effect = aiohttp.ClientResponseError(
        request_info=MagicMock(), history=(), status=status, message="private-key"
    )
    with pytest.raises(error_type) as raised:
        asyncio.run(
            KoitoApi("https://koito.example", "private-key", session).async_get_json(
                "summary"
            )
        )
    assert "private-key" not in str(raised.value)
    assert raised.value.__suppress_context__ is True


def test_timeout_is_a_library_connection_error():
    session = MagicMock()
    session.get.side_effect = TimeoutError("private endpoint")
    with pytest.raises(KoitoConnectionError) as raised:
        asyncio.run(
            KoitoApi("https://koito.example", "key", session).async_get_json("summary")
        )
    assert "private endpoint" not in str(raised.value)
