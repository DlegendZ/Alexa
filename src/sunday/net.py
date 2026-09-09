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
    data: dict[str, Any] | None = None,
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
                if max_bytes is None:
                    response = client.request(
                        method, url, params=params, headers=headers, data=data
                    )
                else:
                    response = _capped(
                        client, method, url, params, headers, max_bytes
                    )
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
                return response
        if attempts < 2:
            time.sleep(RETRY_DELAY_S)
    raise HttpError(last)


def _capped(
    client: httpx.Client,
    method: str,
    url: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    max_bytes: int,
) -> httpx.Response:
    """Stream the body and stop reading at the cap.

    Checking `len(response.content)` afterwards rejects an oversized page but
    only after the whole of it is already in memory, which is the one thing the
    cap exists to prevent.
    """
    with client.stream(method, url, params=params, headers=headers) as response:
        if response.status_code >= 400:
            response.read()
            return response
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise HttpError(f"response larger than the {max_bytes} byte cap")
            chunks.append(chunk)
        # httpx needs the body set before .text is readable on a streamed response.
        response._content = b"".join(chunks)  # noqa: SLF001 - no public setter
        return response


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    return json_from(
        request("GET", url, params=params, headers=headers, timeout=timeout), url
    )


def post_json(
    url: str,
    *,
    data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    """A form-encoded POST. Only OAuth needs one, and it needs it twice."""
    return json_from(
        request("POST", url, data=data, headers=headers, timeout=timeout), url
    )


def json_from(response: httpx.Response, url: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise HttpError(f"{_host(url)} did not return JSON") from None


def _host(url: str) -> str:
    try:
        return httpx.URL(url).host or url
    except Exception:  # pragma: no cover - defensive
        return url
