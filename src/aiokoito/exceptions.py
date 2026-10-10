"""Errors raised by the Koito client, without credential-bearing HTTP details."""


class KoitoError(Exception):
    """Base error for expected Koito failures."""


class KoitoAuthenticationError(KoitoError):
    """The server rejected the API key."""


class KoitoConnectionError(KoitoError):
    """The server could not be reached or returned an HTTP failure."""

    def __init__(
        self,
        message: str = "Unable to connect to Koito",
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class KoitoResponseError(KoitoError, ValueError):
    """The server returned invalid or unsupported data."""
