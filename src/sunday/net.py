"""HTTP with the tool error policy baked in.

One retry after a second on a timeout or a 5xx, no retry on a 4xx, and a
readable string instead of an exception at the end of it. A tool never crashes
the turn.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

RETRY_DELAY_S = 1.0
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class HttpError(Exception):
    """Carries a message already fit to hand to the model."""


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
    max_bytes: int | None = None,
    follow_redirects: bool = False,
) -> httpx.Response:
    """One HTTP call under the retry policy. Raises HttpError with a readable
    message when it finally gives up."""
    attempts = 0
    last: str = "unknown error"
    while attempts < 2:
        attempts += 1
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=follow_redirects,
                max_redirects=1,
            ) as client:
                response = client.request(method, url, params=params, headers=headers)
        except httpx.TimeoutException:
            last = f"timed out after {timeout:.0f}s"
        except httpx.HTTPError as exc:
            last = f"network error: {type(exc).__name__}"
        else:
            if 400 <= response.status_code < 500:
                raise HttpError(f"HTTP {response.status_code} from {_host(url)}")
            if response.status_code >= 500:
                last = f"HTTP {response.status_code} from {_host(url)}"
            else:
                if max_bytes is not None and len(response.content) > max_bytes:
                    raise HttpError(
                        f"response larger than the {max_bytes} byte cap"
                    )
                return response
        if attempts < 2:
            time.sleep(RETRY_DELAY_S)
    raise HttpError(last)


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> Any:
    response = request("GET", url, params=params, timeout=timeout)
    try:
        return response.json()
    except ValueError:
        raise HttpError(f"{_host(url)} did not return JSON") from None


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host or url
    except Exception:  # pragma: no cover - defensive
        return url
