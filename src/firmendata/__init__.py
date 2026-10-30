"""Official Python client for the firmendata API: German, Swiss and Austrian company data.

Autocomplete is free and needs no API key::

    from firmendata import FirmenData

    for hit in FirmenData().autocomplete("siemens")["data"]:
        print(hit["display_name"], hit["eu_id"])

Everything else needs a key from https://firmendata.com/de/developer/api-keys::

    fd = FirmenData(api_key="firmendata_live_...")
    company = fd.get_company("DEF1103R.HRB12300B")
"""

from ._base import REALTIME_TIMEOUT, AppInfo, ResponseInfo
from ._feeds import AsyncFeedIterator, FeedIterator
from ._pagination import SEARCH_RESULT_CAP
from ._version import __version__
from .async_client import AsyncFirmenData
from .client import FirmenData
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    ConflictError,
    FirmenDataError,
    InsufficientCreditsError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TokenExpiredError,
    ValidationError,
)

__all__ = [
    "__version__",
    "FirmenData",
    "AsyncFirmenData",
    "FeedIterator",
    "AsyncFeedIterator",
    "AppInfo",
    "ResponseInfo",
    "REALTIME_TIMEOUT",
    "SEARCH_RESULT_CAP",
    # errors
    "FirmenDataError",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "AuthenticationError",
    "TokenExpiredError",
    "InsufficientCreditsError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
]
