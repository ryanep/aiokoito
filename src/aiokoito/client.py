"""Read-only API client for Koito's web API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urljoin

import aiohttp

from .exceptions import (
    KoitoAuthenticationError,
    KoitoConnectionError,
    KoitoResponseError,
)
from .models import (
    ImageList,
    KoitoData,
    MusicItem,
    NowPlaying,
    RankedItem,
    SimpleArtist,
    Summary,
)

API_PREFIX = "/apis/web/v1"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)
_SUMMARY_METRICS = (
    "plays",
    "minutes_listened",
    "unique_tracks",
    "unique_albums",
    "unique_artists",
)
_SUMMARY_RANKINGS = ("top_artists", "top_albums", "top_tracks")


class KoitoApi:
    """Make authenticated read requests to a Koito instance."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        session: aiohttp.ClientSession,
        *,
        refresh_timeout: float = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = session
        self.refresh_timeout = refresh_timeout

    async def async_get_json(
        self, path: str, params: Mapping[str, str | int] | None = None
    ) -> dict[str, Any]:
        """Fetch a JSON object using GET and Koito's API-key header."""
        endpoint = path.lstrip("/")
        url = f"{self.base_url}{API_PREFIX}/{endpoint}"
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params)}"
        request_kwargs: dict[str, Any] = {
            "headers": {"Authorization": f"Token {self.api_key}"},
            "timeout": REQUEST_TIMEOUT,
        }

        try:
            async with self.session.get(url, **request_kwargs) as response:
                response.raise_for_status()
                payload = await response.json()
        except aiohttp.ContentTypeError:
            raise KoitoResponseError(
                "Koito returned an invalid JSON content type"
            ) from None
        except aiohttp.ClientResponseError as err:
            if err.status in {401, 403}:
                raise KoitoAuthenticationError("Koito rejected the API key") from None
            raise KoitoConnectionError(
                f"Koito request failed (HTTP {err.status})",
                status=err.status,
                retry_after=_retry_after(
                    err.headers.get("Retry-After") if err.headers else None
                ),
            ) from None
        except (aiohttp.ClientError, TimeoutError):
            raise KoitoConnectionError("Unable to connect to Koito") from None
        except ValueError:
            raise KoitoResponseError("Koito returned invalid JSON") from None

        if not isinstance(payload, dict):
            raise KoitoResponseError("Koito API response must be a JSON object")
        return payload

    async def async_fetch_data(self, *, period: str = "all_time") -> KoitoData:
        """Fetch summary and playback concurrently within one refresh deadline."""
        summary_task = asyncio.create_task(self.async_get_summary(period=period))
        playback_task = asyncio.create_task(self._async_get_optional_now_playing())
        try:
            async with asyncio.timeout(self.refresh_timeout):
                summary, now_playing = await asyncio.gather(summary_task, playback_task)
                return KoitoData(summary, now_playing)
        except TimeoutError:
            # Optional playback must not discard statistics already received.
            # result() also preserves an observed auth/backoff failure at the
            # deadline boundary instead of treating it as a playback outage.
            completed_playback = (
                playback_task.result()
                if playback_task.done() and not playback_task.cancelled()
                else None
            )
            if summary_task.done() and not summary_task.cancelled():
                return KoitoData(summary_task.result(), completed_playback)
            raise KoitoConnectionError("Koito refresh timed out") from None
        finally:
            summary_task.cancel()
            playback_task.cancel()
            await asyncio.gather(summary_task, playback_task, return_exceptions=True)

    async def async_get_summary(self, *, period: str = "all_time") -> Summary:
        """Fetch statistics and all returned rankings for the selected period."""
        payload = await self.async_get_json("summary", {"period": period})
        validate_summary(payload)
        return Summary(
            **{field: payload[field] for field in _SUMMARY_METRICS},
            top_artists=_parse_rankings(payload["top_artists"]),
            top_albums=_parse_rankings(payload["top_albums"]),
            top_tracks=_parse_rankings(payload["top_tracks"]),
            title=_text(payload.get("title")),
            avg_minutes_listened_per_day=_integer(
                payload.get("avg_minutes_listened_per_day")
            ),
            avg_plays_per_day=_number(payload.get("avg_plays_per_day")),
            new_tracks=_integer(payload.get("new_tracks")),
            new_albums=_integer(payload.get("new_albums")),
            new_artists=_integer(payload.get("new_artists")),
        )

    async def _async_get_optional_now_playing(self) -> NowPlaying | None:
        """Isolate playback outages, but respect authentication and server backoff."""
        try:
            return await self._async_get_now_playing()
        except KoitoConnectionError as err:
            if err.status == 429 or err.retry_after is not None:
                raise
            return None
        except KoitoResponseError:
            return None

    async def _async_get_now_playing(self) -> NowPlaying:
        """Fetch and validate Koito's optional cached now-playing signal."""
        payload = await self.async_get_json("now-playing")
        currently_playing = payload.get("currently_playing")
        if type(currently_playing) is not bool:
            raise KoitoResponseError("Invalid Koito now-playing response")
        track = payload.get("track")
        if currently_playing and not isinstance(track, dict):
            raise KoitoResponseError("Invalid Koito now-playing response")
        return NowPlaying(
            currently_playing, _parse_music(track) if isinstance(track, dict) else None
        )


def _parse_rankings(records: list[Any]) -> tuple[RankedItem, ...]:
    """Validate each ranked record rather than confusing malformed data with empty."""
    result = []
    for record in records:
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("item"), dict)
            or type(record.get("rank")) is not int
            or record["rank"] < 1
        ):
            raise KoitoResponseError("Invalid Koito ranked record")
        result.append(RankedItem(record["rank"], _parse_music(record["item"])))
    return tuple(result)


def validate_summary(summary: Mapping[str, Any]) -> None:
    """Validate the summary fields required by sensors and ranked summaries."""
    for field in _SUMMARY_METRICS:
        value = summary.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise KoitoResponseError("Invalid Koito summary response")
    for field in _SUMMARY_RANKINGS:
        if not isinstance(summary.get(field), list):
            raise KoitoResponseError("Invalid Koito summary response")


def _artists(value: Any) -> tuple[SimpleArtist, ...]:
    """Parse artist references without truncating names or collections."""
    if not isinstance(value, list):
        return ()
    artists = []
    for artist in value:
        name = _text(artist.get("name") if isinstance(artist, Mapping) else artist)
        if name is not None:
            artists.append(
                SimpleArtist(
                    name,
                    _integer(artist.get("id")) if isinstance(artist, Mapping) else None,
                )
            )
    return tuple(artists)


def _text(value: object) -> str | None:
    """Validate optional text without changing valid API values."""
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if type(value) is int else None


def _number(value: object) -> float | None:
    return value if type(value) is int or type(value) is float else None


def _boolean(value: object) -> bool | None:
    return value if type(value) is bool else None


def _image(value: object) -> ImageList | str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return ImageList(
            **{
                size: _text(value.get(size))
                for size in ("xs", "small", "medium", "large", "xl")
            }
        )
    return None


def _parse_music(value: Mapping[str, Any]) -> MusicItem:
    """Parse the artist/album/track fields without presentation policy."""
    aliases = value.get("aliases")
    return MusicItem(
        id=_integer(value.get("id")),
        name=_text(value.get("name")),
        title=_text(value.get("title")),
        artists=_artists(value.get("artists")),
        image=_image(value.get("image")),
        album_id=_integer(value.get("album_id")),
        musicbrainz_id=_text(value.get("musicbrainz_id")),
        aliases=tuple(alias for alias in aliases if isinstance(alias, str))
        if isinstance(aliases, list)
        else (),
        listen_count=_integer(value.get("listen_count")),
        time_listened=_integer(value.get("time_listened")),
        first_listen=_integer(value.get("first_listen")),
        all_time_rank=_integer(value.get("all_time_rank")),
        duration=_integer(value.get("duration")),
        is_primary=_boolean(value.get("is_primary")),
        is_various_artists=_boolean(value.get("is_various_artists")),
    )


def resolve_image_url(
    image_value: ImageList | str | Mapping[str, str] | None, base_url: str, size: str
) -> str | None:
    """Resolve a Koito image variant against the instance URL."""
    if isinstance(image_value, ImageList):
        image_path = getattr(image_value, size, None)
    elif isinstance(image_value, Mapping):
        image_path = image_value.get(size)
    else:
        image_path = image_value
    if not isinstance(image_path, str) or not image_path.strip():
        return None
    return urljoin(f"{base_url.rstrip('/')}/", image_path)


def _retry_after(value: str | None) -> float | None:
    """Parse HTTP delay seconds or HTTP-date; ignore unusable values."""
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdecimal():
        try:
            return float(int(value))
        except (ValueError, OverflowError):
            return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        return max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None
