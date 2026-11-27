"""Accesso HTTP minimale con User-Agent generico, pause tra richieste e retry."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "draftbot/0.1 (local draft assistant)"
_last_call: dict[str, float] = {}


class NetError(RuntimeError):
    pass


def _throttle(host: str, min_interval: float) -> None:
    wait = _last_call.get(host, 0.0) + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call[host] = time.monotonic()


def request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
            min_interval: float = 0.0, retries: int = 3, timeout: float = 60.0):
    host = urllib.parse.urlsplit(url).netloc
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    h.update(headers or {})
    for attempt in range(retries + 1):
        _throttle(host, min_interval)
        try:
            return urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise NetError(f"HTTP {e.code} su {url}") from e
        except urllib.error.URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise NetError(f"rete non raggiungibile per {url}: {e.reason}") from e
    raise NetError(f"richiesta fallita: {url}")


def get_json(url: str, **kw: Any) -> Any:
    kw.setdefault("headers", {"Accept": "application/json"})
    with request(url, **kw) as r:
        return json.load(r)


def post_json(url: str, payload: Any, **kw: Any) -> Any:
    kw["headers"] = {"Accept": "application/json", "Content-Type": "application/json"}
    with request(url, data=json.dumps(payload).encode(), **kw) as r:
        return json.load(r)

