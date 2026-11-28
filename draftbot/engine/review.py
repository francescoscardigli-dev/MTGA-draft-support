"""Revisione post-draft (Fase 8): pick per pick, la scelta fatta contro quella del motore.

Per ogni pick con busta e carte prese note: il motore valuta tutte le opzioni con il pool di quel
momento (e il wheel, se disponibile). Si registrano la posizione della scelta fatta nella classifica
del motore e la differenza di valore con la prima opzione, in punti di win rate attesa del mazzo
finale. La somma e' una stima, non una certezza: il valore e' quello del modello (Fase 4), con i
suoi limiti (impegno sui colori troppo precoce nei primi pick, sinergie escluse).
"""

from __future__ import annotations

from ..config import format_for
from .pick import recommend


def review_draft(eng, draft) -> dict:
    fmt = format_for(eng.format, eng.set_code)
    book = eng.projector.book
    names = eng.names

    def cards(ids):
        return [book.cards[names[g]] for g in ids if g in names and names[g] in book.cards]

    wheel_fn = (lambda rem: eng.wheel.survival(rem, fmt, eng.pop)) if eng.wheel is not None else None
    rows, pool_ids = [], []
    for p in draft.picks:
        if p.pack_cards is None or not p.picked:
            pool_ids += p.picked
            continue
        pool, pack = cards(pool_ids), cards(p.pack_cards)
        taken = sorted(names.get(g, f"#{g}") for g in p.picked)
        imit = (eng.imitation, eng.pop) if getattr(eng, "imitation", None) is not None else None
        opts, probs = recommend(eng.projector, pool, pack, k=10_000, wheel=wheel_fn, imitation=imit)
        ranking = [tuple(sorted(o.names)) for o in opts]
        rank = ranking.index(tuple(taken)) + 1 if tuple(taken) in ranking else None
        best = opts[0] if opts else None
        mine = opts[rank - 1] if rank else None
        rows.append({
            "pack": p.pack + 1, "pick": p.pick + 1, "taken": taken,
            "best": list(best.names) if best else [], "rank": rank, "n_options": len(opts),
            "value_taken": mine.value if mine else None, "value_best": best.value if best else None,
            "loss": max(best.value - mine.value, 0.0) if (best and mine) else None,
            "p_taken": mine.p_strong if mine else None, "p_best": best.p_strong if best else None,
            "top3": [list(o.names) for o in opts[:3]],
            "archetypes": dict(sorted(probs.items(), key=lambda kv: -kv[1])[:3]),
        })
        pool_ids += p.picked
    scored = [r for r in rows if r["rank"] is not None]
    final = cards(draft.pool if draft.pool else draft.picked_cards())
    builds = eng.builder.best_builds(final, k=3)
    worst = sorted((r for r in scored if r["loss"]), key=lambda r: -r["loss"])[:5]
    return {
        "draft_id": draft.draft_id, "set": eng.set_code, "format": eng.format,
        "picks": rows,
        "summary": {
            "n_picks": len(scored),
            "top1": sum(r["rank"] == 1 for r in scored) / len(scored) if scored else None,
            "top3": sum(r["rank"] <= 3 for r in scored) / len(scored) if scored else None,
            "total_loss": sum(r["loss"] or 0 for r in scored),
            "worst": worst,
        },
        "builds": [{"label": b.label, "expected_wr": b.expected_wr, "lands": b.n_lands, "basics": b.basics,
                    "spells": [c.name for c in b.spells]} for b in builds],
    }


def format_review(rv: dict) -> str:
    s = rv["summary"]
    out = [f"Revisione del draft {rv['draft_id']} ({rv['set']} {rv['format']})"]
    if not s["n_picks"]:
        return out[0] + "\nNessun pick con busta e carta presa nel log."
    out.append(f"Pick valutati: {s['n_picks']}; uguali alla prima scelta del motore: {s['top1']:.0%}; "
               f"tra le prime 3: {s['top3']:.0%}")
    out.append(f"Valore perso stimato (somma, punti di win rate attesa): {s['total_loss'] * 100:.2f}")
    if s["worst"]:
        out.append("Pick piu' costosi secondo il modello:")
        for r in s["worst"]:
            out.append(f"  P{r['pack']}p{r['pick']}: preso {' + '.join(r['taken'])}; il motore preferiva "
                       f"{' + '.join(r['best'])} ({r['loss'] * 100:+.2f} punti, posizione {r['rank']} di {r['n_options']})")
    if rv["builds"]:
        b = rv["builds"][0]
        out.append(f"Build migliore del pool: {b['label']}, win rate attesa {b['expected_wr']:.1%}")
    return "\n".join(out)
