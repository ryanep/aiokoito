# aiokoito

An asynchronous, read-only Python client for
[Koito](https://github.com/gabehf/Koito), a self-hosted music listening tracker.

## Overview

`aiokoito` provides typed access to listening summaries, ranked artists/albums/
tracks, and now-playing data. It uses `aiohttp` and returns immutable dataclasses.

- Fetch statistics and rankings from Koito's summary endpoint.
- Fetch the current playback flag and track details.
- Preserve complete text, artist references, artwork variants, and listening
  metadata without imposing display limits.
- Handle authentication, malformed responses, timeouts, and server backoff.

The client supports these parts of Koito's web API; it is not a wrapper for every
Koito endpoint. It makes GET requests only.

## Requirements

- Python **3.12 or newer**.
- A reachable Koito server and a Koito API key.

`aiohttp` is installed automatically as a dependency.

## Installation

Install from PyPI:

```bash
python -m pip install aiokoito
```

## Quick start

```python
import asyncio

import aiohttp

from aiokoito import KoitoApi


async def main():
    async with aiohttp.ClientSession() as session:
        api = KoitoApi("https://koito.example", "your-api-key", session)
        data = await api.async_fetch_data()

        print("Plays:", data.summary.plays)
        print("Minutes listened:", data.summary.minutes_listened)

        playback = data.now_playing
        if playback is not None and playback.currently_playing:
            if playback.track is not None:
                print("Now playing:", playback.track.title)


asyncio.run(main())
```

Pass the Koito server's base URL, not an API endpoint URL. Requests authenticate
with `Authorization: Token <key>`. The session belongs to the caller: create,
reuse, and close it yourself; the client never closes it.

## API and models

### Listening data

```python
data = await api.async_fetch_data(period="all_time")
summary = await api.async_get_summary(period="all_time")
```

`async_fetch_data()` returns `KoitoData` and makes two concurrent requests:
`summary` and `now-playing`. Counts and rankings come from the same summary
response. Playback is fetched separately, so the result is not an atomic server
snapshot.

`async_get_summary()` returns `Summary` with one request and does not fetch
playback. It can also be used to validate a connection and its credentials.
Both methods accept a Koito period string; the default is `all_time`.

### Data models

All models are frozen, slotted dataclasses. Collections are tuples.

| Model | Contents |
| --- | --- |
| `KoitoData` | Listening summary and optional now-playing data |
| `Summary` | Play counts, listening time, unique/new item counts, averages, and ranked collections |
| `RankedItem` | A `rank` and its music `item` |
| `MusicItem` | Artist, album, or track fields, including IDs and listening metadata |
| `SimpleArtist` | An artist's name and optional ID |
| `ImageList` | Original artwork paths for `xs`, `small`, `medium`, `large`, and `xl` |
| `NowPlaying` | The `currently_playing` flag and optional track |

`summary.top_artists`, `summary.top_albums`, and `summary.top_tracks` contain all
ranked records returned by Koito. Empty rankings are empty tuples. Artist names
and IDs are available through each item's `artists` collection:

```python
for ranked in data.summary.top_albums:
    album = ranked.item
    artists = ", ".join(artist.name for artist in album.artists)
    print(ranked.rank, album.title, artists)
```

Valid text is preserved, including whitespace. Missing or invalid optional
fields become missing typed values. Invalid required summary counts or malformed
ranking structure raise `KoitoResponseError`.

### Playback

`data.now_playing` is `None` when optional playback data is unavailable. When
present, it retains the server's track even if playback is inactive. Use track
details as current playback only when `currently_playing` is true.

### Artwork

Artwork paths remain unchanged in `item.image`. Resolve a size against the
instance URL when an absolute URL is needed:

```python
from aiokoito import resolve_image_url

if data.summary.top_albums:
    album = data.summary.top_albums[0].item
    image_url = resolve_image_url(album.image, api.base_url, "medium")
    print(image_url)
```

The helper returns `None` when the requested artwork is missing.

### Other JSON endpoints

`async_get_json(path, params=None)` makes a read-only request to another endpoint
under `/apis/web/v1` and returns a JSON object. These responses are raw
dictionaries rather than typed models.

## Error handling

All expected client exceptions inherit from `KoitoError`:

| Exception | Meaning |
| --- | --- |
| `KoitoAuthenticationError` | The server rejected the API key with HTTP 401 or 403 |
| `KoitoConnectionError` | A transport failure, timeout, or other HTTP error |
| `KoitoResponseError` | Invalid JSON, content type, or response structure |

`KoitoConnectionError.status` contains the HTTP status, or `None` for transport
failures. Its `retry_after` contains a server-requested delay in seconds, or
`None`. Both delay-seconds and HTTP-date `Retry-After` headers are supported.

```python
from aiokoito import (
    KoitoAuthenticationError,
    KoitoConnectionError,
    KoitoResponseError,
)

try:
    data = await api.async_fetch_data()
except KoitoAuthenticationError:
    print("The API key was rejected")
except KoitoConnectionError as error:
    if error.retry_after is not None:
        print(f"Wait {error.retry_after} seconds before retrying")
    else:
        print("Koito could not be reached or returned an HTTP error")
except KoitoResponseError:
    print("Koito returned an invalid response")
```

These snippets run inside an async function, using the client from the quick
start. The client does not automatically retry. Exception messages omit
credentials and request details; unexpected programming errors propagate.

### Optional playback and timeouts

Normal playback transport or payload failures return `now_playing=None` while
preserving summary data. Authentication errors, HTTP 429, and HTTP errors with
server backoff propagate to the caller.

Individual requests have a ten-second timeout. A complete refresh also has a
ten-second deadline by default, configurable in seconds:

```python
api = KoitoApi(base_url, api_key, session, refresh_timeout=5)
```

If summary has completed when playback alone reaches the deadline, summary data
is returned with playback unavailable. If summary reaches the deadline, the
refresh fails. Failures, deadlines, and caller cancellation cancel and await
pending sibling requests.

## Development

Clone the repository and install the package and development tools into a
virtual environment:

```bash
git clone https://github.com/ryanep/aiokoito.git
cd aiokoito
python -m venv .venv
source .venv/bin/activate
python -m pip install -e . pytest pytest-asyncio build
python -m pytest tests
python -m build
```

Tests run without Home Assistant or a live Koito server. Build artifacts are
written to `dist/`. The release workflow builds and tests version tags before
publishing through PyPI trusted publishing.

Report bugs and request features through the
[issue tracker](https://github.com/ryanep/aiokoito/issues).

## License

MIT. See [license](license).
