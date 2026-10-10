"""API values and structure survive the typed parsing boundary."""

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

from aiokoito import KoitoApi
import pytest


def fetch_music(item, now_playing, field="top_albums"):
    api = KoitoApi("https://koito.example", "key", MagicMock())
    api.async_get_json = AsyncMock(
        side_effect=[
            {
                "plays": 42,
                "minutes_listened": 120,
                "unique_tracks": 9,
                "unique_albums": 4,
                "unique_artists": 3,
                "top_artists": [],
                "top_albums": [],
                "top_tracks": [],
                "title": "All time",
                "avg_plays_per_day": 1.25,
                "new_albums": 2,
                field: [{"item": item, "rank": 1}],
            },
            now_playing,
        ]
    )
    return asyncio.run(api.async_fetch_data())


def test_client_preserves_full_strings_artists_images_and_metadata():
    title = "  " + "x" * 500 + "  "
    names = ["y" * 500] * 100
    data = fetch_music(
        {
            "id": 42,
            "title": title,
            "artists": [
                {"id": index, "name": name} for index, name in enumerate(names)
            ],
            "image": {"medium": "/image.webp", "large": "/large.webp"},
            "musicbrainz_id": "mbid",
            "listen_count": 3000,
            "time_listened": 9000,
            "first_listen": 123,
            "all_time_rank": 1,
            "is_various_artists": True,
        },
        {"currently_playing": False, "track": {"title": "Stale"}},
    )
    record = data.summary.top_albums[0]
    album = record.item
    assert record.rank == 1
    assert album.id == 42
    assert album.title == title
    assert tuple(artist.name for artist in album.artists) == tuple(names)
    assert album.artists[-1].id == 99
    assert album.image.medium == "/image.webp"
    assert album.image.large == "/large.webp"
    assert album.listen_count == 3000
    assert album.time_listened == 9000
    assert album.first_listen == 123
    assert album.all_time_rank == 1
    assert album.musicbrainz_id == "mbid"
    assert album.is_various_artists is True
    assert data.summary.title == "All time"
    assert data.summary.avg_plays_per_day == 1.25
    assert data.summary.new_albums == 2
    assert data.now_playing.track.title == "Stale"
    with pytest.raises(FrozenInstanceError):
        album.title = "Changed"
    with pytest.raises(FrozenInstanceError):
        album.artists[0].name = "Changed"


@pytest.mark.parametrize("title", [None, True, 123, {}, []])
def test_invalid_optional_fields_are_missing_not_raw_json(title):
    data = fetch_music(
        {"title": title, "artists": "invalid", "image": 1},
        {"currently_playing": True, "track": {"title": title, "album_id": True}},
    )
    assert data.summary.top_albums[0].item.title is None
    assert data.summary.top_albums[0].item.artists == ()
    assert data.summary.top_albums[0].item.image is None
    assert data.now_playing.track.album_id is None


def test_artist_and_active_track_metadata_are_preserved():
    data = fetch_music(
        {"name": "Artist", "aliases": ["Alias", None], "is_primary": True},
        {
            "currently_playing": True,
            "track": {
                "id": 9,
                "title": "Song",
                "album_id": 42,
                "duration": 180,
                "image": "/track.webp",
                "artists": [{"id": 1, "name": "Artist"}, None],
            },
        },
        field="top_artists",
    )
    artist = data.summary.top_artists[0].item
    assert artist.aliases == ("Alias",)
    assert artist.is_primary is True
    track = data.now_playing.track
    assert track.id == 9
    assert track.album_id == 42
    assert track.duration == 180
    assert track.image == "/track.webp"
    assert len(track.artists) == 1
