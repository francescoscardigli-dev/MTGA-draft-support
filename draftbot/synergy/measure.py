"""Misura delle sinergie sui game data: regressione regolarizzata sul residuo dei mazzi.

Per ogni mazzo si calcola il residuo della win rate rispetto al valutatore di mazzo della Fase 4
(qualita' delle carte, curva, colori, con il livello del giocatore come controllo). La qualita'
delle carte include gia' l'effetto medio di ogni carta; una sinergia e' cio' che resta: un payoff
che rende di piu' quando nel mazzo ci sono piu' enabler.

Caratteristiche per tema t (contate tra le carte non base del mazzo):
  e_t, p_t        enabler e payoff (effetti principali: servono a non scambiare per sinergia
                  un effetto lineare del numero di carte)
  x_t = e_t * p_t / 10            interazione (temi con payoff)
  x_t = m_t (m_t - 1) / 20        densita' (temi di sole keyword: m = carte del tema)

Ridge pesata (pesi = partite), stimata per colori dei mazzi (le coppie con abbastanza mazzi) e su
tutti i mazzi. La verifica e' fuori campione: stima fino al giorno di taglio, errore sui mazzi dopo.
Si tengono solo i temi la cui interazione ha lo stesso segno nei due periodi e migliora la previsione.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..engine.book import CardBook
from ..engine.deckmodel import CardArrays, DeckModel, _plain, deck_matrix_features, load_decks
from ..stats.prior import PRIORS_DIR
from .tags import TagSet

INTER_SCALE = 10.0


@dataclass
class ThemeEffect:
    theme: str
    kind: str                  # interazione | densita'
    coef: float                # punti di win rate (frazione) per unita' di x_t
    se: float
    coef_late: float | None = None
    n_decks_with: int = 0


@dataclass
class SynergyModel:
    set_code: str
    format: str
    effects: dict[str, float]               # tema -> coefficiente tenuto (0 = scartato)
    details: list[dict]
    cutoff: str | None
    oos_gain: float                         # riduzione dell'errore fuori campione (frazione dell'MSE)
    oos_gain_se: float
    fitted_at: str
    oos_slope: float = 0.0                  # pendenza del residuo reale sulla sinergia prevista (atteso 1)
    oos_slope_se: float = 0.0
    oos_effect_sd: float = 0.0              # dispersione della sinergia prevista tra i mazzi (punti)
    notes: list[str] = field(default_factory=list)

    def save(self, path: Path | None = None) -> Path:
        path = path or PRIORS_DIR / f"{self.set_code}_{self.format}_synergy.yaml"
        head = "# Effetti di sinergia misurati da 'python -m draftbot synergy'. Non modificare a mano.\n"
        path.write_text(head + yaml.safe_dump(_plain(asdict(self)), sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return path


def load_synergy(set_code: str, fmt: str, path: Path | None = None) -> SynergyModel | None:
    path = path or PRIORS_DIR / f"{set_code}_{fmt}_synergy.yaml"
    if not path.is_file():
        return None
    return SynergyModel(**yaml.safe_load(path.read_text(encoding="utf-8")))


def theme_matrices(names: list[str], tags: TagSet) -> dict[str, tuple[np.ndarray, np.ndarray, str]]:
    """tema -> (vettore enabler, vettore payoff, tipo) allineati a ``names``."""
    out = {}
    idx = {n: i for i, n in enumerate(names)}
    for t, d in tags.themes.items():
        e = np.zeros(len(names))
        p = np.zeros(len(names))
        for n in d.get("enablers", []):
            if n in idx:
                e[idx[n]] = 1
        for n in d.get("payoffs", []):
            if n in idx:
                p[idx[n]] = 1
        kind = "interazione" if p.any() and not t.startswith("kw:") else "densita'"
        if t.startswith("kw:"):
            e = np.maximum(e, p)       # per i temi di keyword contano tutte le carte che la citano
        out[t] = (e, p, kind)
    return out


def theme_features(D: np.ndarray, mats: dict[str, tuple[np.ndarray, np.ndarray, str]]) -> pd.DataFrame:
    D = D.astype(float)
    f = {}
    for t, (e, p, kind) in mats.items():
        ne, np_ = D @ e, D @ p
        if kind == "interazione":
            f[f"{t}|e"] = ne
            f[f"{t}|p"] = np_
            f[f"{t}|x"] = ne * np_ / INTER_SCALE
        else:
            f[f"{t}|e"] = ne
            f[f"{t}|x"] = ne * np.maximum(ne - 1, 0) / (2 * INTER_SCALE)
    return pd.DataFrame(f)


def _ridge(X: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray]:
    mu = np.average(X, axis=0, weights=w)
    sd = np.sqrt(np.average((X - mu) ** 2, axis=0, weights=w))
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    W = w / w.mean()
    A = (Z * W[:, None]).T @ Z + lam * np.eye(Z.shape[1])
    beta_z = np.linalg.solve(A, (Z * W[:, None]).T @ (y - np.average(y, weights=w)))
    resid = y - np.average(y, weights=w) - Z @ beta_z
    s2 = np.average(resid ** 2, weights=w)
    cov = s2 * np.linalg.inv(A) @ ((Z * W[:, None]).T @ Z) @ np.linalg.inv(A)
    return beta_z / sd, np.sqrt(np.diag(cov)) / sd


def residuals(model: DeckModel, book: CardBook, deck: pd.DataFrame, info: pd.DataFrame) -> np.ndarray:
    ca = CardArrays.from_book(list(deck.columns), book)
    F = deck_matrix_features(deck.to_numpy(), ca, info["colors"], info["splash"], book)
    score = F.apply(lambda r: model.score(r.to_dict()), axis=1).to_numpy()
    pl = info["player"].fillna(info["player"].mean()).to_numpy()
    pred = model.intercept + score + model.control_coef * pl
    return (info["wins"] / info["games"]).to_numpy() - pred


def measure(model: DeckModel, book: CardBook, tags: TagSet, set_code: str, fmt: str, cutoff: str,
            lam: float = 50.0, min_decks: int = 300) -> SynergyModel:
    deck, info = load_decks(set_code, fmt)
    names = list(deck.columns)
    mats = theme_matrices(names, tags)
    nb = np.array([bool(book.get(n)) and not book.get(n).is_basic for n in names])
    D = deck.to_numpy() * nb
    F = theme_features(D, mats)
    r = residuals(model, book, deck, info)
    w = info["games"].to_numpy(float)
    early = (info["day"] <= cutoff).to_numpy()
    late = ~early
    cols = list(F.columns)
    X = F.to_numpy()
    b_e, se_e = _ridge(X[early], r[early], w[early], lam)
    b_l, _ = _ridge(X[late], r[late], w[late], lam)
    details, keep = [], {}
    for t, (e, p, kind) in mats.items():
        j = cols.index(f"{t}|x")
        n_with = int(((D @ e) > 0).sum())
        details.append(asdict(ThemeEffect(t, kind, float(b_e[j]), float(se_e[j]), float(b_l[j]), n_with)))
        # si tiene un tema se l'interazione e' positiva, almeno 2 errori standard, e stabile nel tempo
        if b_e[j] > 2 * se_e[j] and b_l[j] > 0 and n_with >= min_decks:
            keep[t] = float(b_e[j])
    # verifica fuori campione: previsione del residuo dei mazzi dopo il taglio con i soli temi tenuti
    xs = [cols.index(f"{t}|x") for t in keep]
    pred = X[late][:, xs] @ np.array([keep[t] for t in keep]) if xs else np.zeros(late.sum())
    pred = pred - np.average(pred, weights=w[late]) if xs else pred
    y = r[late] - np.average(r[late], weights=w[late])
    base = np.average(y ** 2, weights=w[late])
    gain = 1 - np.average((y - pred) ** 2, weights=w[late]) / base
    # errore standard del guadagno con bootstrap sui mazzi
    rng = np.random.default_rng(0)
    gains = []
    for _ in range(200):
        ii = rng.integers(0, late.sum(), late.sum())
        b = np.average(y[ii] ** 2, weights=w[late][ii])
        gains.append(1 - np.average((y[ii] - pred[ii]) ** 2, weights=w[late][ii]) / b)
    slope = slope_se = 0.0
    if xs and np.std(pred) > 0:
        Xs = np.column_stack([np.ones(len(pred)), pred])
        W = w[late]
        A = (Xs * W[:, None]).T @ Xs
        beta = np.linalg.solve(A, (Xs * W[:, None]).T @ y)
        res = y - Xs @ beta
        cov = np.average(res ** 2, weights=W) * np.linalg.inv(A)
        slope, slope_se = float(beta[1]), float(np.sqrt(cov[1, 1]))
    return SynergyModel(set_code, fmt, keep, sorted(details, key=lambda d: -d["coef"]), cutoff, float(gain),
                        float(np.std(gains)), date.today().isoformat(), slope, slope_se,
                        float(np.sqrt(np.average(pred ** 2, weights=w[late]))) if xs else 0.0,
                        [f"ridge lambda {lam}", "tenuti: interazione > 2 SE nel periodo di stima e > 0 dopo",
                         f"mazzi: {early.sum()} prima del taglio, {late.sum()} dopo"])
