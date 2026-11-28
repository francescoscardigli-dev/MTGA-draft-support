"""Modelli della Fase 7 salvati per l'uso: wheel e segnali.

- ``priors/<formato>_extras.yaml``: coefficienti del wheel (generici per formato) e pesi dei segnali
  (0 se nella verifica non hanno migliorato la previsione dei colori finali).
- Attese dei segnali per set: si calcolano dai draft data del set (qualita' disponibile per colore a
  ogni turno) e si mettono in cache in ``data/``. Per TRK bastano i draft data, gia' pubblicati.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ..paths import data_dir
from ..stats.prior import PRIORS_DIR
from .deckmodel import _plain
from .signals import SignalBaseline, SignalModel
from .wheel import LogitWheel


MIN_SIGNAL_GAIN = 0.01    # guadagno minimo di log-verosimiglianza media (su FRA due verifiche danno +-0,007: rumore)


@dataclass
class Extras:
    format: str
    wheel: LogitWheel
    signal: SignalModel | None
    fitted_on: str
    fitted_at: str
    notes: list[str] = field(default_factory=list)

    def save(self, path: Path | None = None) -> Path:
        path = path or PRIORS_DIR / f"{self.format}_extras.yaml"
        head = "# Modelli di wheel e segnali generati da 'python -m draftbot fit-extras'. Non modificare a mano.\n"
        path.write_text(head + yaml.safe_dump(_plain(asdict(self)), sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return path


def load_extras(fmt: str, path: Path | None = None) -> Extras | None:
    path = path or PRIORS_DIR / f"{fmt}_extras.yaml"
    if not path.is_file():
        return None
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Extras(d["format"], LogitWheel(**d["wheel"]), SignalModel(**d["signal"]) if d.get("signal") else None,
                  d["fitted_on"], d["fitted_at"], d.get("notes") or [])


def signal_baseline(con, set_code: str, fmt: str, book, n_drafts: int = 400) -> SignalBaseline:
    cache = data_dir() / f"signal_baseline_{set_code}_{fmt}.json"
    if cache.is_file():
        d = json.loads(cache.read_text())
        return SignalBaseline({tuple(map(int, k.split(","))): v for k, v in d.items()})
    from .backtest import _cards, read_drafts
    drafts = read_drafts(set_code, fmt, max_drafts=n_drafts, seed=4)
    base = SignalBaseline.fit((p.pack_number, p.pick_number, _cards(book, p.pack)) for d in drafts for p in d.picks)
    cache.write_text(json.dumps({f"{a},{b}": v for (a, b), v in base.expected.items()}))
    return base


def fit_extras(con, set_code: str, fmt: str, n_drafts: int = 600, early_temp: float = 1.0, flow: bool = True,
               log=print) -> Extras:
    """Stima su un set con game data: wheel su tutti i draft del campione, segnali con verifica
    (prima meta' dei giorni per stimare, seconda per verificare); i segnali si tengono solo se migliorano."""
    from ..config import format_for
    from ..stats.prior import load_prior
    from .backtest import _cards, archetype_samples, final_colors, read_drafts
    from .book import load_book
    from .deckmodel import load_deck_model
    from .phase7check import popularity
    from .projection import ArchetypeModel, Projector
    from .signals import fit_signal_model
    from .wheel import fit_logit_wheel, wheel_samples

    fmt_cfg = format_for(fmt, set_code)
    prior = load_prior(fmt)
    book = load_book(con, set_code, fmt, prior=prior, half_life=prior.half_life if prior else None)
    model = load_deck_model(fmt)
    proj = Projector(book, model, fmt_cfg, ArchetypeModel(model.lambdas, mix=model.arch_mix))
    days = [r[0] for r in con.execute("SELECT DISTINCT day FROM stat_picks WHERE set_code=? AND format=? ORDER BY day",
                                      (set_code, fmt))]
    mid = days[len(days) // 2]
    fit_d = read_drafts(set_code, fmt, until=mid, max_drafts=n_drafts, seed=1)
    test_d = read_drafts(set_code, fmt, since=mid, max_drafts=n_drafts // 2, seed=2)
    pop = popularity(con, set_code, fmt, None)
    wheel = fit_logit_wheel(list(wheel_samples(fit_d + test_d, book, fmt_cfg)), fmt_cfg, pop)
    base = SignalBaseline.fit((p.pack_number, p.pick_number, _cards(book, p.pack)) for d in fit_d for p in d.picks)
    sig = fit_signal_model(proj, fit_d, test_d, final_colors(set_code, fmt), base)
    notes = [f"segnali verificati dopo il {mid}: guadagno di log-verosimiglianza {sig.ll_gain:+.4f}"]
    if sig.ll_gain < MIN_SIGNAL_GAIN:
        notes.append(f"segnali disattivati: guadagno fuori campione sotto la soglia {MIN_SIGNAL_GAIN}")
        sig = SignalModel(0.0, 0.0, sig.ll_gain, sig.notes)
    if flow:
        from .signals import fit_future_shift
        delta, se, n = fit_future_shift(proj, fit_d + test_d, final_colors(set_code, fmt), base)
        sig.future_shift, sig.future_shift_se = max(delta, 0.0), se
        notes.append(f"flusso dei colori: delta {delta:+.3f} +- {se:.3f} su {n} campioni")
    sig.early_temp = early_temp
    notes.append(f"impegno nella busta 1: lambda x{early_temp}")
    log("; ".join(notes))
    return Extras(fmt, wheel, sig, set_code, date.today().isoformat(), notes)
