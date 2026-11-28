"""Dataset pubblici di 17Lands (CC BY 4.0).

Si usano SOLO i file elencati in https://www.17lands.com/public_datasets e il file pubblico
cards.csv. Gli endpoint che alimentano le pagine del sito (/api/card_data, /card_ratings/data,
/color_ratings/data) sono riservati a 17Lands.com e non vanno usati.
"""

from __future__ import annotations

import csv
import gzip
import io
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import net

INDEX_URL = "https://www.17lands.com/api/public_datasets"
CARDS_CSV_URL = "https://data.17landscapes.com/analysis_data/cards/cards.csv"
ATTRIBUTION = "Dati: 17Lands (https://www.17lands.com), licenza CC BY 4.0"

CARD_PREFIXES = {
    "draft": ("pack_card_", "pool_"),
    "game": ("deck_", "sideboard_", "opening_hand_", "drawn_", "tutored_"),
}


@dataclass(frozen=True)
class Dataset:
    expansion: str
    event_type: str
    kind: str  # draft | game | replay
    url: str
    last_updated: str


def list_datasets() -> list[Dataset]:
    out = []
    for e in net.get_json(INDEX_URL):
        for kind, url in (e.get("urls") or {}).items():
            out.append(Dataset(e["expansion"], e["event_type"], kind, url, e.get("last_updated", "")))
    return out


def download(url: str, dest: Path) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with net.request(url, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)
    return dest


def read_cards_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def header_from_gzip_stream(stream: io.BufferedIOBase) -> list[str]:
    with gzip.open(stream, "rt", newline="", encoding="utf-8") as g:
        return next(csv.reader(g))


def remote_header(url: str) -> list[str]:
    """Legge solo l'intestazione di un dataset remoto, senza scaricarlo tutto."""
    with net.request(url, timeout=120) as r:
        return header_from_gzip_stream(r)


def card_names_from_header(header: list[str], kind: str = "draft") -> list[str]:
    prefix = CARD_PREFIXES[kind][0]
    return [c[len(prefix):] for c in header if c.startswith(prefix)]


def dataset_meta(ds: Dataset) -> dict[str, Any]:
    return {"expansion": ds.expansion, "event_type": ds.event_type, "kind": ds.kind,
            "url": ds.url, "last_updated": ds.last_updated}


def remote_first_pack_size(url: str, max_rows: int = 3000) -> int | None:
    """Dimensione piu' frequente della busta al primo pick, leggendo solo l'inizio del file remoto."""
    from collections import Counter
    with net.request(url, timeout=120) as r, gzip.open(r, "rt", newline="", encoding="utf-8") as g:
        rd = csv.reader(g)
        header = next(rd)
        pick_i = header.index("pick_number")
        pack_i = [i for i, c in enumerate(header) if c.startswith("pack_card_")]
        sizes: Counter = Counter()
        for n, row in enumerate(rd):
            if n >= max_rows:
                break
            if row[pick_i] == "0":
                sizes[sum(int(row[i] or 0) for i in pack_i)] += 1
    return sizes.most_common(1)[0][0] if sizes else None
