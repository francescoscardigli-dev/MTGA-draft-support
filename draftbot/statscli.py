"""Comandi CLI della Fase 3: update-data, stats, fit-prior, calibrate."""

from __future__ import annotations

import argparse

from . import db
from .config import load_format, load_set
from .sources.seventeenlands import ATTRIBUTION
from .stats import calibrate as cal
from .stats.ingest import ingest
from .stats.prior import DEFAULT_FEATURES, FEATURE_SETS, fit, load_prior, tables_for
from .stats.ratings import Filters, ratings

SORTS = {"bayes": "gih_bayes", "gih": "gih_wr", "iwd": "iwd", "ata": "ata", "alsa": "alsa", "oh": "oh_wr",
         "games": "n_gih", "prior": "prior_wr"}


def add_parsers(sub: argparse._SubParsersAction) -> None:
    up = sub.add_parser("update-data", help="scarica e aggrega i dataset pubblici 17Lands di un set")
    up.add_argument("code")
    up.add_argument("--format", dest="fmt", help="un solo formato (default: tutti quelli del set)")
    up.add_argument("--kind", choices=["game", "draft"], help="solo game data o solo draft data")
    up.add_argument("--force", action="store_true", help="riscarica anche se la cache e' aggiornata")

    st = sub.add_parser("stats", help="statistiche per carta (GIH WR bayesiana, IWD, ATA, ALSA...)")
    st.add_argument("code")
    st.add_argument("--format", dest="fmt", default="premier")
    st.add_argument("--colors", help="solo mazzi con questi colori principali, es. WU")
    st.add_argument("--tier", action="append", choices=["low", "mid", "high"], help="fascia dei giocatori")
    st.add_argument("--as-of", help="solo dati fino a questo giorno (AAAA-MM-GG)")
    st.add_argument("--half-life", type=float,
                    help="decadimento temporale in giorni (default: quello del prior; 0 = nessuno)")
    st.add_argument("--sort", choices=sorted(SORTS), default="bayes")
    st.add_argument("--asc", action="store_true", help="ordine crescente")
    st.add_argument("--name", help="filtra per nome")
    st.add_argument("--limit", type=int, default=25)
    st.add_argument("--no-prior", action="store_true", help="restringe verso la media del set invece che verso il prior")

    fp = sub.add_parser("fit-prior", help="stima il prior della GIH WR sui set indicati (scrive priors/<formato>.yaml)")
    fp.add_argument("--format", dest="fmt", default="premier")
    fp.add_argument("--sets", nargs="+", required=True)
    fp.add_argument("--features", choices=sorted(FEATURE_SETS), default=DEFAULT_FEATURES)
    fp.add_argument("--ridge", type=float, default=1.0)
    fp.add_argument("--half-life", type=float, help="emivita consigliata da salvare nel prior (giorni)")

    ca = sub.add_parser("calibrate", help="verifica prior, blend bayesiano e decadimento su un set con game data")
    ca.add_argument("code")
    ca.add_argument("--format", dest="fmt", default="premier")
    ca.add_argument("--prior-sets", nargs="+", required=True, help="set usati per il prior (oltre a quello verificato)")


def _pct(x: float) -> str:
    return "   -  " if x != x else f"{x * 100:5.1f}%"


def _num(x: float, w: int = 5) -> str:
    return " " * (w - 1) + "-" if x != x else f"{x:{w}.2f}"


def run(args: argparse.Namespace) -> int:
    con = db.connect()
    if args.command == "update-data":
        cfg = load_set(args.code)
        fmts = [args.fmt] if args.fmt else list(cfg.formats)
        kinds = [args.kind] if args.kind else ["game", "draft"]
        for f in fmts:
            for k in kinds:
                r = ingest(cfg, f, k, force=args.force)
                if r:
                    print(f"{cfg.code} {f} {k}: {r.rows} righe, dal {r.first_day} al {r.last_day} "
                          f"(snapshot {r.snapshot_id})" + (f"; {r.note}" if r.note else ""))
                    if r.unmapped:
                        print(f"  carte senza corrispondenza nel database: {', '.join(r.unmapped[:10])}")
        print(ATTRIBUTION)
        return 0

    if args.command == "fit-prior":
        load_format(args.fmt)
        tables = tables_for(con, [load_set(s).code for s in args.sets], args.fmt)
        m = fit(tables, args.fmt, feature_set=args.features, ridge=args.ridge)
        m.half_life = args.half_life
        m.drift_sd, notes = cal.estimate_drift(con, m.train_sets, args.fmt, args.half_life)
        m.notes += notes
        path = m.save()
        print(f"Prior {args.fmt} stimato su {', '.join(m.train_sets)} ({m.n_cards} carte): "
              f"tau = {m.tau2 ** 0.5 * 100:.2f} punti, GIH WR media {m.baseline_gih * 100:.1f}%, "
              f"deriva {m.drift_sd * 100:.2f} punti, emivita {m.half_life or 'nessuna'}")
        for k, v in m.coef.items():
            print(f"  {k:<12} {v * 100:+.2f} punti per deviazione standard")
        print(f"Scritto: {path}")
        return 0

    if args.command == "calibrate":
        code = load_set(args.code).code
        prior_sets = [load_set(s).code for s in args.prior_sets if load_set(s).code != code]
        print(f"# Calibrazione {code} {args.fmt}\n")
        print(cal.format_report("Prior, set escluso dalla stima (leave-one-set-out)",
                                cal.loso(con, [code] + prior_sets, args.fmt)))
        prior = fit(tables_for(con, prior_sets, args.fmt), args.fmt)
        saved = load_prior(args.fmt)
        prior.half_life = saved.half_life if saved else None
        prior.drift_sd, _ = cal.estimate_drift(con, prior_sets, args.fmt, prior.half_life)
        print(f"Emivita {prior.half_life or 'nessuna'} (da priors/{args.fmt}.yaml), deriva stimata su "
              f"{', '.join(prior_sets)}: {prior.drift_sd * 100:.2f} punti\n")
        print(cal.format_report(f"Divisione temporale su {code} (prior stimato su {', '.join(prior_sets)})",
                                cal.time_split(con, code, args.fmt, prior)))
        label, scores = cal.decay_test(con, code, args.fmt)
        print(cal.format_report("Decadimento temporale", {label: scores}))
        print(ATTRIBUTION)
        return 0

    # stats
    cfg = load_set(args.code)
    prior = None if args.no_prior else load_prior(args.fmt)
    hl = args.half_life if args.half_life is not None else (prior.half_life if prior else None)
    f = Filters(colors=args.colors, tiers=tuple(args.tier) if args.tier else None, as_of=args.as_of,
                half_life=hl or None)
    t = ratings(con, cfg.code, args.fmt, f, prior=prior)
    c = t.cards
    if args.name:
        c = c[c["card"].str.contains(args.name, case=False, regex=False)]
    c = c.sort_values(SORTS[args.sort], ascending=args.asc, na_position="last")
    fmt = load_format(args.fmt)
    print(f"{cfg.code} {fmt.display_name}" + (f", mazzi {f.norm_colors()}" if f.colors else "")
          + (f", fascia {'+'.join(f.tiers)}" if f.tiers else "") + (f", fino al {f.as_of}" if f.as_of else "")
          + (f", emivita {f.half_life:g} giorni (partite pesate)" if f.half_life else ""))
    print(f"Dati dal {t.days[0]} al {t.days[1]}; partite: {t.n_games:.0f}; "
          f"GIH WR media: {_pct(t.baseline_gih) if t.baseline_gih else '-'}; "
          f"prior: {'stima della carta su tutti i mazzi' if f.colors else ('modello ' + '+'.join(prior.train_sets) if prior else 'media del set')}; "
          f"tau: {t.tau2 ** 0.5 * 100:.2f} punti ({t.tau2_source})" if t.tau2 else "")
    if not t.n_games:
        print("Nessun game data: la stima e' il solo prior (da ATA, ALSA e rarita').")
    print(f"{'carta':<34} {'stima':>6} {'+-':>5} {'GIH':>6} {'n GIH':>7} {'OH':>6} {'GNS':>6} {'IWD':>6} "
          f"{'ATA':>5} {'ALSA':>5} {'prior':>6}")
    for r in c.head(args.limit).itertuples():
        sd = "  -  " if r.gih_sd_next != r.gih_sd_next else f"{r.gih_sd_next * 100:5.1f}"
        print(f"{r.card[:34]:<34} {_pct(r.gih_bayes)} {sd} {_pct(r.gih_wr)} {r.n_gih:7.0f} {_pct(r.oh_wr)} "
              f"{_pct(r.gns_wr)} {_pct(r.iwd) if r.iwd == r.iwd else '   -  '} {_num(r.ata)} {_num(r.alsa)} "
              f"{_pct(r.prior_wr)}")
    print(ATTRIBUTION)
    return 0
