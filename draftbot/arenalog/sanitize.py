"""Pulizia di Player.log prima di salvarlo come fixture di test.

Strategia (prudente):
1. per default si tengono SOLO le righe che il parser riconosce come messaggi di draft (buste,
   pick, iscrizione all'evento, pool) e la riga di intestazione che le precede;
2. nelle righe tenute si cancellano i valori delle chiavi sensibili, token JWT, email, nomi
   utente nei percorsi, nomi visualizzati "Nome#12345" e le stringhe passate con ``--redact``;
3. UUID e ID account vengono sostituiti da pseudonimi coerenti (lo stesso valore diventa sempre
   lo stesso pseudonimo), cosi' i collegamenti tra messaggi dello stesso draft restano;
4. alla fine si ricontrolla l'output: se trova ancora qualcosa di sospetto, lo segnala.

Gli ID delle carte (grpId) e i nomi degli eventi non sono dati personali e restano.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .parser import LogParser, read_lines

SENSITIVE_KEYS = [
    "screenName", "displayName", "playerName", "opponentScreenName", "userName",
    "userId", "accountId", "personaId", "playerId", "clientId", "sessionId", "deviceId", "machineId",
    "platformId", "steamId", "epicId", "email", "emailAddress", "ipAddress", "ip", "country", "birthday",
    "access_token", "refresh_token", "accessToken", "refreshToken", "token", "ticket", "authorization",
    "password", "credentials", "externalId", "transactionId", "receipt", "wotcId", "matchmakerAccountId",
]
_KEYS = "|".join(sorted((re.escape(k) for k in SENSITIVE_KEYS), key=len, reverse=True))
# chiave sensibile con valore stringa, a qualsiasi livello di escape (\" , \\\" ...)
_STR_VALUE = re.compile(r'(?P<q>\\*)"(?P<k>' + _KEYS + r')(?P=q)"(?P<sep>\s*:\s*)(?P=q)"(?P<v>.*?)(?P=q)"',
                        re.IGNORECASE)
_NUM_VALUE = re.compile(r'(?P<q>\\*)"(?P<k>' + _KEYS + r')(?P=q)"(?P<sep>\s*:\s*)(?P<v>-?\d+)', re.IGNORECASE)
_JWT = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_WIN_USER = re.compile(r"([A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+)([^\\/\s\"']+)", re.IGNORECASE)
_MAC_USER = re.compile(r"(/(?:Users|home)/)([^/\s\"']+)")
_DISPLAY_NAME = re.compile(r"[^\s\"'\\:,{}\[\]#]{2,}#\d{5}\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_ACCOUNT_ID = re.compile(r"\b[A-Z0-9]{26}\b")       # formato degli ID account Wizards
_BEARER = re.compile(r"(Bearer\s+)[\w.\-+/=]+", re.IGNORECASE)


@dataclass
class SanitizeReport:
    lines_in: int = 0
    lines_out: int = 0
    replaced: Counter[str] = field(default_factory=Counter)
    kinds: Counter[str] = field(default_factory=Counter)
    leftovers: list[str] = field(default_factory=list)   # righe ancora sospette (numero: motivo)

    def summary(self) -> str:
        out = [f"Righe lette: {self.lines_in}, tenute: {self.lines_out}"]
        if self.kinds:
            out.append("Messaggi di draft tenuti: " + ", ".join(f"{k} {v}" for k, v in sorted(self.kinds.items())))
        out.append("Sostituzioni: " + (", ".join(f"{k} {v}" for k, v in sorted(self.replaced.items())) or "nessuna"))
        if self.leftovers:
            out.append(f"ATTENZIONE: {len(self.leftovers)} righe ancora sospette, controllale a mano:")
            out += [f"  {x}" for x in self.leftovers[:20]]
        else:
            out.append("Controllo finale: nessun dato sospetto trovato.")
        return "\n".join(out)


class Sanitizer:
    def __init__(self, redact: Iterable[str] = ()):
        self.redact = [r for r in redact if r and r.strip()]
        self._pseudo: dict[str, str] = {}
        self.report = SanitizeReport()

    def _pseudonym(self, kind: str, value: str) -> str:
        key = f"{kind}:{value.lower()}"
        if key not in self._pseudo:
            n = sum(1 for k in self._pseudo if k.startswith(kind + ":")) + 1
            self._pseudo[key] = (f"00000000-0000-4000-8000-{n:012d}" if kind == "uuid" else f"ACCOUNT{n:019d}")
        self.report.replaced[kind] += 1
        return self._pseudo[key]

    def clean_line(self, line: str) -> str:
        r = self.report.replaced

        def sub(pattern: re.Pattern[str], repl, s: str, label: str) -> str:
            s2, n = pattern.subn(repl, s)
            if n:
                r[label] += n
            return s2

        s = line
        for text in self.redact:
            s = sub(re.compile(re.escape(text), re.IGNORECASE), "REDACTED_TEXT", s, "testo indicato")
        s = sub(_JWT, "REDACTED_JWT", s, "token JWT")
        s = sub(_BEARER, r"\1REDACTED", s, "token Bearer")
        s = sub(_STR_VALUE, lambda m: f'{m["q"]}"{m["k"]}{m["q"]}"{m["sep"]}{m["q"]}"REDACTED{m["q"]}"', s,
                "chiave sensibile")
        s = sub(_NUM_VALUE, lambda m: f'{m["q"]}"{m["k"]}{m["q"]}"{m["sep"]}0', s, "chiave sensibile")
        s = sub(_EMAIL, "redacted@example.invalid", s, "email")
        s = sub(_WIN_USER, r"\1USER", s, "utente nel percorso")
        s = sub(_MAC_USER, r"\1USER", s, "utente nel percorso")
        s = sub(_DISPLAY_NAME, "REDACTED#00000", s, "nome visualizzato")
        s = _UUID.sub(lambda m: self._pseudonym("uuid", m.group(0)), s)
        s = _ACCOUNT_ID.sub(lambda m: self._pseudonym("id account", m.group(0))
                            if not m.group(0).isdigit() else m.group(0), s)
        return s

    def check(self, line: str) -> str | None:
        """Motivo per cui una riga gia' pulita sembra ancora contenere dati personali."""
        for text in self.redact:
            if text.lower() in line.lower():
                return "contiene un testo da cancellare"
        if _JWT.search(line):
            return "token JWT"
        if _EMAIL.search(line) and "example.invalid" not in line:
            return "email"
        m = re.search(r'\\*"(' + _KEYS + r')\\*"\s*:\s*\\*"(?!REDACTED)([^"\\]+)', line, re.IGNORECASE)
        if m:
            return f"chiave {m.group(1)}"
        return None


def sanitize_lines(lines: Iterable[str], *, redact: Iterable[str] = (), keep_all: bool = False
                   ) -> tuple[list[str], SanitizeReport]:
    san = Sanitizer(redact)
    parser = LogParser()
    out: list[str] = []
    prev: str | None = None
    prev_kept = False
    for line in lines:
        san.report.lines_in += 1
        kinds = parser.feed_line(line)
        san.report.kinds.update(kinds)
        if keep_all or kinds:
            # la riga di intestazione (es. "<== EventJoin(...)" o il timestamp) precede il JSON
            if not keep_all and prev is not None and not prev_kept and "{" not in prev:
                out.append(san.clean_line(prev))
            out.append(san.clean_line(line))
            prev_kept = True
        else:
            prev_kept = False
        prev = line
    for i, ln in enumerate(out, 1):
        why = san.check(ln)
        if why:
            san.report.leftovers.append(f"riga {i}: {why}")
    san.report.lines_out = len(out)
    return out, san.report


def sanitize_file(src: Path, dst: Path, *, redact: Iterable[str] = (), keep_all: bool = False) -> SanitizeReport:
    if src.resolve() == dst.resolve():
        raise ValueError("il file di output deve essere diverso da quello di input")
    out, rep = sanitize_lines(read_lines(src), redact=redact, keep_all=keep_all)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return rep
