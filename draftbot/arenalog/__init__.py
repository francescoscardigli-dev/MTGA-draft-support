"""Lettura e pulizia di Player.log di MTG Arena (Fase 2).

ATTENZIONE: il formato del log non e' documentato da Wizards. La struttura qui implementata
viene da fonti non ufficiali (tool open source) e NON e' ancora verificata su log reali
dell'utente. Il parser e' scritto per essere tollerante e per segnalare (non indovinare)
tutto cio' che non torna con la config del formato.
"""

from .parser import Draft, LogParser, Pick, parse_files, parse_text
from .paths import default_log_paths, find_logs
from .validate import validate

__all__ = ["Draft", "LogParser", "Pick", "parse_files", "parse_text", "default_log_paths", "find_logs",
           "validate"]
