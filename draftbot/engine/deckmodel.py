"""Valutatore di mazzo: win rate attesa di un mazzo, stimata dai game data 17Lands.

Regressione lineare pesata (pesi = partite) della win rate del mazzo su:
  mean_q   qualita' media delle magie (le terre stanno nei posti delle terre: contano con il fixing)
  top3     qualita' media delle 3 magie migliori
  arch     win rate dell'archetipo (coppia di colori principali) rispetto alla media
  n_le2    carte non terra di costo <= 2;   n_ge5  carte non terra di costo >= 5
  lands    terre - 17;   splash  1 se il mazzo ha uno splash;   extra_colors  colori principali oltre 2
  fix_2c, fix_splash, fix_3c   terre di fixing (non base, producono almeno 2 colori del mazzo, fino a 4)
           nei mazzi a 2 colori, con splash, a 3 colori (Fase 10: su 8 set e formati valgono +0,1/+1 punti
           l'una, soprattutto nei mazzi a 3 colori)
  player   win rate del giocatore (solo variabile di controllo: non entra nel punteggio del mazzo)

Il controllo sul giocatore separa l'effetto del mazzo da quello di chi lo gioca. Il modello e' per
formato; i coefficienti dipendono dal set (es. in FRA pagano i mazzi aggressivi), quindi si stimano
sul set stesso quando ci sono i game data e, altrimenti, sui set precedenti.

Il file ``priors/<formato>_deck.yaml`` contiene anche il "profilo" di un mazzo finale tipico:
la qualita' media della carta al posto i (1 = la migliore) nei mazzi a due colori dei giocatori
forti. Serve a proiettare il mazzo finale a meta' draft (vedi projection.py).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..config import format_for, load_format, load_set
from ..paths import raw_dir
from ..stats.prior import PRIORS_DIR
from .book import CardBook

FEATURES = ["mean_q", "top3", "arch", "n_le2", "n_ge5", "lands", "splash", "extra_colors",
            "fix_2c", "fix_splash", "fix_3c"]
FIX_CAP = 4
CONTROL = "player"
DECK_SLOTS = 23


@dataclass
class DeckModel:
    format: str
    intercept: float
    coef: dict[str, float]
    control_coef: float
    profile: list[float]           # qualita' attesa della carta al posto i del mazzo finale
    playable_share: float          # quota dei pick che finisce nel mazzo
    train_sets: list[str]
    n_decks: int
    as_of: str | None
    fitted_at: str
    notes: list[str] = field(default_factory=list)
    type_share: dict[str, float] = field(default_factory=dict)   # quota di mazzi 2c / splash / 3c
    lambdas: list[float] | None = None     # modello degli archetipi (projection.fit_lambda)
    lambda_set: str | None = None
    tiebreak: float = 0.0
    arch_mix: list[float] | None = None

    def score(self, x: dict[str, float]) -> float:
        """Win rate attesa relativa (senza intercetta): differenze tra mazzi, non valori assoluti."""
        return sum(self.coef.get(k, 0.0) * x.get(k, 0.0) for k in FEATURES)

    def save(self, path: Path | None = None) -> Path:
        path = path or PRIORS_DIR / f"{self.format}_deck.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        head = ("# Modello del mazzo generato da 'python -m draftbot fit-deck-model'. Non modificare a mano.\n"
                "# Contiene solo coefficienti e il profilo di qualita' di un mazzo tipico.\n")
        path.write_text(head + yaml.safe_dump(_plain(asdict(self)), sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        return path


def _plain(x):
    """Converte i tipi numpy in tipi Python (YAML non li sa scrivere)."""
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    return x


def load_deck_model(fmt: str, path: Path | None = None) -> DeckModel | None:
    path = path or PRIORS_DIR / f"{fmt}_deck.yaml"
    if not path.is_file():
        return None
    return DeckModel(**yaml.safe_load(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- caratteristiche

@dataclass
class CardArrays:
    """Vettori per carta allineati alle colonne di una matrice di mazzi."""
    names: list[str]
    q: np.ndarray
    nonbasic: np.ndarray
    spell: np.ndarray
    mv: np.ndarray
    produces: list[str] = field(default_factory=list)

    @classmethod
    def from_book(cls, names: list[str], book: CardBook) -> "CardArrays":
        cs = [book.get(n) for n in names]
        return cls(names,
                   np.array([c.q if c else 0.0 for c in cs]),
                   np.array([bool(c) and not c.is_basic for c in cs]),
                   np.array([bool(c) and not c.is_land for c in cs]),
                   np.array([c.mv if c else 0.0 for c in cs]),
                   [(c.produces if c and c.is_land and not c.is_basic else "") for c in cs])


def _topk_mean(D: np.ndarray, q: np.ndarray, mask: np.ndarray, k: int) -> np.ndarray:
    idx = np.where(mask)[0]
    idx = idx[np.argsort(-q[idx])]
    cnt = D[:, idx]
    cum = np.cumsum(cnt, axis=1)
    take = np.clip(np.minimum(cnt, k - (cum - cnt)), 0, None)
    return (take * q[idx]).sum(1) / np.maximum(take.sum(1), 1)


def deck_matrix_features(D: np.ndarray, ca: CardArrays, main_colors: pd.Series, splash: pd.Series,
                         book: CardBook) -> pd.DataFrame:
    D = D.astype(float)
    nb = D[:, ca.nonbasic]
    f = pd.DataFrame(index=range(len(D)))
    sp = D[:, ca.spell]
    f["mean_q"] = (sp * ca.q[ca.spell]).sum(1) / np.maximum(sp.sum(1), 1)
    f["top3"] = _topk_mean(D, ca.q, ca.spell, 3)
    mc = main_colors.fillna("").astype(str).map(lambda s: "".join(c for c in "WUBRG" if c in s)).to_numpy()
    spc = splash.fillna("").astype(str).map(lambda s: "".join(c for c in "WUBRG" if c in s)).to_numpy()
    f["arch"] = [book.arch_delta.get(m, 0.0) for m in mc]
    f["n_le2"] = D[:, ca.spell & (ca.mv <= 2)].sum(1)
    f["n_ge5"] = D[:, ca.spell & (ca.mv >= 5)].sum(1)
    f["lands"] = D[:, ~ca.spell].sum(1) - 17
    f["splash"] = splash.fillna("").astype(str).str.len().gt(0).astype(float).to_numpy()
    f["extra_colors"] = np.clip(np.array([len(m) for m in mc]) - 2, 0, None)
    fix = fixing_counts(D, ca, mc, spc)
    three = np.array([len(m) >= 3 for m in mc])
    spl = f["splash"].to_numpy() > 0
    f["fix_2c"] = np.where(~three & ~spl, fix, 0.0)
    f["fix_splash"] = np.where(~three & spl, fix, 0.0)
    f["fix_3c"] = np.where(three, fix, 0.0)
    return f


def fixing_counts(D: np.ndarray, ca: CardArrays, main: np.ndarray, splash: np.ndarray) -> np.ndarray:
    """Terre di fixing per mazzo (copie, fino a FIX_CAP): non base che producono >= 2 colori del mazzo."""
    land_idx = [j for j, p in enumerate(ca.produces) if p] if ca.produces else []
    out = np.zeros(len(D))
    if not land_idx:
        return out
    cols = np.array([m + s for m, s in zip(main, splash)], dtype=object)
    for key in set(cols):
        rows = np.where(cols == key)[0]
        js = [j for j in land_idx if sum(c in key for c in set(ca.produces[j])) >= 2]
        if js:
            out[rows] = D[np.ix_(rows, js)].sum(1)
    return np.minimum(out, FIX_CAP)


def deck_type_share(main_colors: pd.Series, splash: pd.Series, w: np.ndarray | None = None) -> dict[str, float]:
    mc = main_colors.fillna("").astype(str).str.len().to_numpy()
    sp = splash.fillna("").astype(str).str.len().to_numpy() > 0
    w = np.ones(len(mc)) if w is None else w
    kinds = {"2c": (mc < 3) & ~sp, "splash": (mc < 3) & sp, "3c": mc >= 3}
    tot = w.sum()
    return {k: float(w[m].sum() / tot) for k, m in kinds.items()}


def deck_features(counts: dict[str, int], book: CardBook, main_colors: str, splash: str = "") -> dict[str, float]:
    names = list(counts)
    ca = CardArrays.from_book(names, book)
    D = np.array([[counts[n] for n in names]], dtype=float)
    return deck_matrix_features(D, ca, pd.Series([main_colors]), pd.Series([splash]), book).iloc[0].to_dict()


# --------------------------------------------------------------------------- lettura dei game data

def canonical_names(set_code: str, names: list[str]) -> list[str]:
    """Nomi 17Lands -> nomi del catalogo (es. faccia frontale -> "A // B"), come in Fase 3."""
    from .. import db
    from ..stats.ingest import name_map
    mapping, _ = name_map(db.connect(), load_set(set_code).code, names)
    return [mapping[n] for n in names]


def game_data_path(set_code: str, fmt: str) -> Path:
    cfg, f = load_set(set_code), load_format(fmt)
    p = raw_dir("17lands") / f"game_data_public.{cfg.seventeenlands_expansion}.{f.seventeenlands_event_type}.csv.gz"
    if not p.is_file():
        raise FileNotFoundError(f"{p.name} non in cache: esegui python -m draftbot update-data {cfg.code}")
    return p


def load_decks(set_code: str, fmt: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Un mazzo per (draft_id, build_index): carte (matrice) e informazioni (partite, vittorie...)."""
    path = game_data_path(set_code, fmt)
    h = pd.read_csv(path, nrows=0).columns
    cards = [c[5:] for c in h if c.startswith("deck_")]
    meta = ["draft_id", "build_index", "game_time", "main_colors", "splash_colors", "won",
            "user_game_win_rate_bucket"]
    df = pd.read_csv(path, usecols=meta + [f"deck_{c}" for c in cards],
                     dtype={**{f"deck_{c}": np.int8 for c in cards}, **{m: str for m in meta}})
    df["won"] = df["won"].str.lower().eq("true")
    g = df.groupby(["draft_id", "build_index"], sort=False)
    deck = g[[f"deck_{c}" for c in cards]].first()
    deck.columns = canonical_names(set_code, cards)
    deck = deck.T.groupby(level=0).sum().T  # due nomi 17Lands sulla stessa carta: si sommano
    info = g.agg(games=("won", "size"), wins=("won", "sum"), day=("game_time", "min"),
                 colors=("main_colors", "first"), splash=("splash_colors", "first"),
                 player=("user_game_win_rate_bucket", "first"))
    info["day"] = info["day"].str[:10]
    info["player"] = pd.to_numeric(info["player"], errors="coerce")
    return deck, info


# --------------------------------------------------------------------------- stima

def _wls(X: np.ndarray, y: np.ndarray, w: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones(len(X)), X])
    W = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * W[:, None], y * W, rcond=None)
    return beta


def fit_deck_model(con: sqlite3.Connection, books: dict[str, CardBook], fmt: str, *,
                   as_of: str | None = None, high_tier: float = 0.58) -> DeckModel:
    """Stima il modello su uno o piu' set. ``books`` porta la qualita' delle carte (con lo stesso as_of)."""
    feats, ys, ws, ctrl, profiles, nonbasic_counts, shares = [], [], [], [], [], [], []
    for code, book in books.items():
        deck, info = load_decks(code, fmt)
        if as_of:
            keep = (info["day"] <= as_of).to_numpy()
            deck, info = deck[keep], info[keep]
        ca = CardArrays.from_book(list(deck.columns), book)
        D = deck.to_numpy()
        f = deck_matrix_features(D, ca, info["colors"], info["splash"], book)
        feats.append(f)
        ys.append((info["wins"] / info["games"]).to_numpy())
        ws.append(info["games"].to_numpy(float))
        pl = info["player"]
        ctrl.append(pl.fillna(pl.mean()).to_numpy())
        # profilo: mazzi a due colori, senza splash, dei giocatori forti
        sel = ((info["colors"].fillna("").str.len() == 2) & (info["splash"].fillna("") == "")
               & (info["player"] >= high_tier)).to_numpy()
        nonbasic_counts.append(D[:, ca.spell].sum(1) / format_for(fmt, code).pool_size)
        shares.append(deck_type_share(info["colors"], info["splash"]))
        for row in D[sel][:20000]:
            vals = np.repeat(ca.q[ca.spell], row[ca.spell].astype(int))
            vals = np.sort(vals)[::-1][:DECK_SLOTS]
            if len(vals) == DECK_SLOTS:
                profiles.append(vals)
    F = pd.concat(feats, ignore_index=True)
    y, w, c = np.concatenate(ys), np.concatenate(ws), np.concatenate(ctrl)
    X = np.column_stack([F[FEATURES].to_numpy(), c])
    beta = _wls(X, y, w)
    notes = ["win rate per partita, pesi = partite", f"profilo: mazzi a 2 colori dei giocatori >= {high_tier}"]
    arch_i = FEATURES.index("arch")
    if beta[1 + arch_i] < 0:
        # la forza dell'archetipo e' gia' nella qualita' delle carte: un coefficiente negativo
        # allontanerebbe il motore dagli archetipi forti. Si stima senza e si fissa a 0.
        notes.append(f"arch: coefficiente stimato {beta[1 + arch_i]:.3f} < 0, fissato a 0 (gia' nella qualita')")
        X0 = np.delete(X, arch_i, axis=1)
        b0 = _wls(X0, y, w)
        beta = np.insert(b0, 1 + arch_i, 0.0)
    share = float(np.mean(np.concatenate(nonbasic_counts)))   # quota del pool che finisce nel mazzo
    return DeckModel(
        format=fmt, intercept=float(beta[0]), coef={k: float(v) for k, v in zip(FEATURES, beta[1:-1])},
        control_coef=float(beta[-1]), profile=[float(v) for v in np.mean(profiles, axis=0)],
        playable_share=min(share, 0.95), train_sets=list(books), n_decks=len(F), as_of=as_of,
        fitted_at=date.today().isoformat(), notes=notes,
        type_share={k: float(np.mean([s[k] for s in shares])) for k in ("2c", "splash", "3c")})
