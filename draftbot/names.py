"""Normalizzazione dei nomi delle carte tra fonti diverse (Scryfall, 17Lands, Arena)."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})
_SPACED_ELLIPSIS = re.compile(r"\s*\.\s+\.\s+\.")


def normalize(name: str) -> str:
    """Chiave di confronto: minuscole, apostrofi e puntini uniformi, spazi compattati."""
    s = unicodedata.normalize("NFKC", name).translate(_QUOTES)
    s = _SPACED_ELLIPSIS.sub("...", s)
    s = s.replace("…", "...")
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def front_face(name: str) -> str:
    return name.split(" // ")[0]


def keys(name: str) -> list[str]:
    """Chiavi da provare in ordine: nome completo, poi faccia frontale."""
    full = normalize(name)
    front = normalize(front_face(name))
    return [full] if full == front else [full, front]
