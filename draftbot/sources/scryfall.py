"""Download delle carte da Scryfall (API pubblica, max ~10 richieste al secondo)."""

from __future__ import annotations

import urllib.parse
from typing import Any, Iterable

from .. import net

API = "https://api.scryfall.com"
_INTERVAL = 0.12


def get_set(code: str) -> dict[str, Any]:
    return net.get_json(f"{API}/sets/{code.lower()}", min_interval=_INTERVAL)


def all_sets() -> list[dict[str, Any]]:
    return net.get_json(f"{API}/sets", min_interval=_INTERVAL)["data"]


def search(query: str) -> list[dict[str, Any]]:
    url = f"{API}/cards/search?" + urllib.parse.urlencode({"q": query, "unique": "prints", "order": "set"})
    out: list[dict[str, Any]] = []
    while url:
        try:
            page = net.get_json(url, min_interval=_INTERVAL)
        except net.NetError as e:
            if "HTTP 404" in str(e):  # nessun risultato
                return out
            raise
        out.extend(page["data"])
        url = page.get("next_page")
    return out


def set_cards(code: str) -> list[dict[str, Any]]:
    """Tutte le stampe di un set Scryfall."""
    return search(f"e:{code.lower()}")


def cards_by_name(names: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Risolve nomi esatti con /cards/collection (75 per richiesta). Restituisce (carte, non trovati)."""
    names = list(dict.fromkeys(names))
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for i in range(0, len(names), 75):
        batch = names[i:i + 75]
        res = net.post_json(f"{API}/cards/collection", {"identifiers": [{"name": n} for n in batch]},
                            min_interval=_INTERVAL)
        found.extend(res.get("data", []))
        missing.extend(nf.get("name", "?") for nf in res.get("not_found", []))
    return found, missing
