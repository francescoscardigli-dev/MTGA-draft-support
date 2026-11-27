"""Parsing dei costi di mana in pip per colore."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COLORS = "WUBRG"
_SYMBOL = re.compile(r"\{([^}]+)\}")


@dataclass(frozen=True)
class ManaCost:
    raw: str
    generic: int = 0
    pips: dict[str, int] = field(default_factory=dict)  # pip colorati "pieni" (anche ibridi monocolore 2/W)
    hybrid: tuple[str, ...] = ()  # simboli ibridi bicolore, es. "W/U"
    phyrexian: dict[str, int] = field(default_factory=dict)
    colorless: int = 0  # simbolo {C}
    x: int = 0
    snow: int = 0
    mv: int = 0  # mana value calcolato dal costo ({2/W} vale 2, X vale 0)

    @property
    def colors(self) -> str:
        """Colori richiesti dal costo, in ordine WUBRG."""
        used = set(self.pips) | set(self.phyrexian)
        for h in self.hybrid:
            used.update(c for c in h.split("/") if c in COLORS)
        return "".join(c for c in COLORS if c in used)

    def max_pips(self) -> int:
        """Il massimo numero di pip di un singolo colore (indicatore della difficolta' di lancio)."""
        return max(self.pips.values(), default=0)


def parse(cost: str | None) -> ManaCost:
    """Converte "{2}{W}{W/U}{B/P}{X}" in un ManaCost. Stringa vuota o None: costo nullo."""
    if not cost:
        return ManaCost(raw=cost or "")
    generic = colorless = x = snow = mv = 0
    pips: dict[str, int] = {}
    phy: dict[str, int] = {}
    hybrid: list[str] = []
    for sym in _SYMBOL.findall(cost):
        s = sym.upper()
        mv += 1
        if s.isdigit():
            generic += int(s)
            mv += int(s) - 1
        elif s in COLORS:
            pips[s] = pips.get(s, 0) + 1
        elif s == "C":
            colorless += 1
        elif s in ("X", "Y", "Z"):
            x += 1
            mv -= 1
        elif s == "S":
            snow += 1
        elif s.endswith("/P"):
            for c in s[:-2].split("/"):
                if c in COLORS:
                    phy[c] = phy.get(c, 0) + 1
        elif "/" in s:
            a, b = s.split("/", 1)
            if a == "2" and b in COLORS:  # {2/W}: conta come pip del colore, MV 2
                pips[b] = pips.get(b, 0) + 1
                mv += 1
            elif a == "C" and b in COLORS:
                pips[b] = pips.get(b, 0) + 1
            else:
                hybrid.append(s)
        else:
            raise ValueError(f"simbolo di mana sconosciuto: {{{sym}}} in {cost}")
    return ManaCost(cost, generic, pips, tuple(hybrid), phy, colorless, x, snow, mv)
