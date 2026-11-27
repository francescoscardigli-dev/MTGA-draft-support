"""Prior della GIH WR dai set precedenti.

Idea: prima che escano i game data di un set (oggi il caso di TRK) ci sono gia' i draft data, cioe'
come i giocatori scelgono le carte. Su set precedenti con entrambi i dati si stima quanto ATA, ALSA,
taken rate e rarita' predicono la GIH WR (come scarto dalla media del set). Il modello e' una
regressione ridge pesata; la sua varianza residua (tolto il rumore binomiale) e' tau^2, cioe' quanto
fidarsi del prior. Il file ``priors/<formato>.yaml`` e' versionato: dice su quali set e quando e'
stato stimato e non contiene dati di un set specifico.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..config import ROOT
from .ratings import Filters, Table, raw_table

PRIORS_DIR = ROOT / "priors"
RARITIES = ("uncommon", "rare", "mythic")          # common e' la base
FEATURE_SETS = {
    "rarita": ["r_uncommon", "r_rare", "r_mythic"],
    "pick": ["ata_norm", "ata_norm2", "alsa_norm", "taken_rate"],
}
FEATURE_SETS["pick+rarita"] = FEATURE_SETS["pick"] + FEATURE_SETS["rarita"]
DEFAULT_FEATURES = "pick+rarita"
MIN_N_FIT = 300        # partite GIH minime per usare una carta nella stima
MIN_TAKEN = 20         # prese minime per fidarsi di ATA/ALSA della carta


def features(c: pd.DataFrame) -> pd.DataFrame:
    """Feature per carta da una tabella di ratings.raw_table (valori mancanti -> media)."""
    x = pd.DataFrame(index=c.index)
    rar = c["rarity"].fillna("common").str.lower()
    rar = rar.where(rar.isin(("common",) + RARITIES), "mythic")   # special, bonus... come mythic
    for r in RARITIES:
        x[f"r_{r}"] = (rar == r).astype(float)
    reliable = c["n_taken"] >= MIN_TAKEN
    x["ata_norm"] = c["ata_norm"].where(reliable)
    x["ata_norm2"] = x["ata_norm"] ** 2
    x["alsa_norm"] = c["alsa_norm"].where(reliable)
    x["taken_rate"] = c["taken_rate"].where(c["n_seen"] >= MIN_TAKEN)
    return x


@dataclass
class PriorModel:
    format: str
    feature_set: str
    features: list[str]
    intercept: float
    coef: dict[str, float]
    mean: dict[str, float]
    std: dict[str, float]
    fill: dict[str, float]
    tau2: float
    baseline_gih: float                    # GIH WR media dei set di stima (usata se il set non ha game data)
    train_sets: list[str]
    n_cards: int
    fitted_at: str
    ridge: float
    notes: list[str] = field(default_factory=list)
    half_life: float | None = None         # emivita consigliata (giorni), scelta con la calibrazione
    drift_sd: float = 0.0                  # deriva della win rate nel tempo (si somma all'incertezza)

    def predict_delta(self, c: pd.DataFrame) -> np.ndarray:
        x = features(c)[self.features]
        x = x.fillna(self.fill)
        z = (x - pd.Series(self.mean)) / pd.Series(self.std)
        return self.intercept + z.to_numpy() @ np.array([self.coef[k] for k in self.features])

    def save(self, path: Path | None = None) -> Path:
        path = path or PRIORS_DIR / f"{self.format}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = ("# Prior della GIH WR generato da 'python -m draftbot fit-prior'. Non modificare a mano:\n"
                "# rigeneralo. Contiene solo coefficienti, nessun dato di carte.\n")
        path.write_text(head + yaml.safe_dump(asdict(self), sort_keys=False, allow_unicode=True), encoding="utf-8")
        return path


def load_prior(fmt: str, path: Path | None = None) -> PriorModel | None:
    path = path or PRIORS_DIR / f"{fmt}.yaml"
    if not path.is_file():
        return None
    return PriorModel(**yaml.safe_load(path.read_text(encoding="utf-8")))


def training_rows(t: Table, min_n: int = MIN_N_FIT) -> pd.DataFrame:
    c = t.cards.copy()
    c = c[c["n_gih"] >= min_n]
    c["target"] = c["gih_wr"] - t.baseline_gih
    return c


def fit(tables: dict[str, Table], fmt: str, *, feature_set: str = DEFAULT_FEATURES, ridge: float = 1.0,
        min_n: int = MIN_N_FIT) -> PriorModel:
    """Regressione ridge pesata della GIH WR (scarto dalla media del set) sulle feature di pick."""
    feats = FEATURE_SETS[feature_set]
    rows, used = [], []
    for code, t in tables.items():
        if t.baseline_gih is None:
            continue
        r = training_rows(t, min_n)
        if len(r):
            r = r.assign(_set=code)
            rows.append(r)
            used.append(code)
    if not rows:
        raise ValueError("nessun set con game data sufficienti per stimare il prior")
    data = pd.concat(rows, ignore_index=True)
    x = features(data)[feats]
    fill = x.mean().to_dict()
    x = x.fillna(fill)
    mean, std = x.mean(), x.std().replace(0, 1.0)
    z = ((x - mean) / std).to_numpy()
    y = data["target"].to_numpy()
    p = data["gih_wr"].to_numpy()
    noise = p * (1 - p) / data["n_gih"].to_numpy()
    tau2 = float(np.var(y))
    for _ in range(5):  # pesi = 1 / (varianza reale + rumore), stimati iterando
        wts = 1.0 / (tau2 + noise)
        W = np.sqrt(wts)
        A = np.hstack([np.ones((len(z), 1)), z]) * W[:, None]
        reg = ridge * np.eye(A.shape[1])
        reg[0, 0] = 0.0
        beta = np.linalg.solve(A.T @ A + reg, A.T @ (y * W))
        resid = y - np.hstack([np.ones((len(z), 1)), z]) @ beta
        tau2 = float(max(1e-5, np.average(resid ** 2 - noise, weights=wts)))
    base = float(np.mean([tables[s].baseline_gih for s in used]))
    return PriorModel(
        format=fmt, feature_set=feature_set, features=feats, intercept=float(beta[0]),
        coef={k: float(v) for k, v in zip(feats, beta[1:])}, mean=mean.to_dict(), std=std.to_dict(),
        fill={k: float(v) for k, v in fill.items()}, tau2=tau2, baseline_gih=base, train_sets=used,
        n_cards=len(data), fitted_at=date.today().isoformat(), ridge=ridge,
        notes=[f"carte con almeno {min_n} partite GIH", "target: GIH WR - GIH WR media del set"],
    )


def tables_for(con: sqlite3.Connection, sets: list[str], fmt: str, f: Filters = Filters()) -> dict[str, Table]:
    return {s: raw_table(con, s, fmt, f) for s in sets}
