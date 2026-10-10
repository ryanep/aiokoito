"""Immutable typed Koito API records, without presentation limits."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageList:
    """Original artwork paths for each server-provided size."""

    xs: str | None = None
    small: str | None = None
    medium: str | None = None
    large: str | None = None
    xl: str | None = None


@dataclass(frozen=True, slots=True)
class SimpleArtist:
    """Artist reference embedded in an album or track."""

    name: str
    id: int | None = None


@dataclass(frozen=True, slots=True)
class MusicItem:
    """Artist, album or track fields returned by summary and playback."""

    id: int | None = None
    name: str | None = None
    title: str | None = None
    artists: tuple[SimpleArtist, ...] = ()
    image: ImageList | str | None = None
    album_id: int | None = None
    musicbrainz_id: str | None = None
    aliases: tuple[str, ...] = ()
    listen_count: int | None = None
    time_listened: int | None = None
    first_listen: int | None = None
    all_time_rank: int | None = None
    duration: int | None = None
    is_primary: bool | None = None
    is_various_artists: bool | None = None


@dataclass(frozen=True, slots=True)
class RankedItem:
    """A rank and its complete music record."""

    rank: int
    item: MusicItem


@dataclass(frozen=True, slots=True)
class Summary:
    """Listening statistics and ranked collections for the requested period."""

    plays: int = 0
    minutes_listened: int = 0
    unique_tracks: int = 0
    unique_albums: int = 0
    unique_artists: int = 0
    top_artists: tuple[RankedItem, ...] = ()
    top_albums: tuple[RankedItem, ...] = ()
    top_tracks: tuple[RankedItem, ...] = ()
    title: str | None = None
    avg_minutes_listened_per_day: int | None = None
    avg_plays_per_day: float | None = None
    new_tracks: int | None = None
    new_albums: int | None = None
    new_artists: int | None = None


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """Playback flag and track as returned by the server."""

    currently_playing: bool
    track: MusicItem | None = None


@dataclass(frozen=True, slots=True)
class KoitoData:
    """Summary and optional playback fetched during one refresh."""

    summary: Summary
    now_playing: NowPlaying | None = None
