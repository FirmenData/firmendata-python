"""Official Python client for the firmendata German company-data API.

Autocomplete is free and needs no API key::

    from firmendata import FirmenData

    for hit in FirmenData().autocomplete("siemens")["data"]:
        print(hit["name"], hit["eu_id"])

Everything else needs a key from https://firmendata.com/de/account/api-keys::

    fd = FirmenData(api_key="firmendata_live_...")
    company = fd.get_company("DEB1103R_HRB123456")
"""

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
