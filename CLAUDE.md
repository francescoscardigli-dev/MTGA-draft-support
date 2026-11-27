# Contesto per Claude Code

Assistente locale (Python 3.11+) per i draft di MTG Arena: legge `Player.log`, suggerisce i pick
spiegando il punteggio, aiuta col mazzo finale. Formati: solo Premier Draft e Pick Two Draft.
Set di test: TRK (Star Trek, con il foglio bonus SDS). Set per il test di generalità e lo sviluppo
delle statistiche: FRA.

Documenti da leggere prima di lavorare: `docs/plan.md` (piano approvato),
`docs/phase0_report.md` (verifiche), `docs/phase1_report.md` (database carte),
`docs/phase2_report.md` (parser del log).

## Modalità di lavoro richiesta dall'utente

- Si lavora in italiano.
- Si procede fase per fase. A fine fase: eseguire i test, mostrare un esempio di output e
  **fermarsi per l'approvazione** prima della fase successiva.
- Se un dato o una fonte non è disponibile, dirlo invece di inventarlo.
- Nessun dato specifico di un set nel codice del motore (c'è un test che lo controlla): tutto
  sta in `sets/<CODICE>.yaml` e `formats/<nome>.yaml`.
- Branch di lavoro: quello assegnato alla sessione. Fasi 0-1 su `claude/mtg-draft-tool-review-kse14y`,
  Fase 2 su `claude/blissful-tesla-2atj16` (che contiene anche le fasi precedenti).

## Decisioni prese (approvate)

- **Solo dati pubblici 17Lands** (CC BY 4.0): l'indice `https://www.17lands.com/api/public_datasets`,
  i file su `data.17landscapes.com` e `cards.csv`. **Non usare** `/api/card_data`,
  `/card_ratings/data`, `/color_ratings/data`: la loro risposta dice che sono solo per 17Lands.com.
- Le statistiche si sviluppano e calibrano su **FRA** (ha game data Premier e Pick Two). Per TRK si
  aggiornano quando 17Lands pubblica i game data (al 2026-11-26 ci sono solo i draft data).
- UI: web locale (FastAPI con WebSocket). Modulo regole: rinviato.
- Config di formato separata. Struttura verificata sui dati: Premier ha tavolo da 8 e wheel al
  pick 9; Pick Two ha tavolo da 4, 2 carte per pick e wheel al pick 5.
- Il suggerimento restituisce k carte (in Pick Two si valutano le coppie).
- I tag LLM di meccaniche e archetipi si fanno nella fase delle sinergie (Fase 5).

## Stato al 2026-11-27 (aggiornato a fine sessione Fase 2)

| Fase | Stato |
|---|---|
| 0 Verifiche e scheletro | completata |
| 1 Database carte, ID Arena, `add-set` | completata (TRK 341 carte, FRA 295, nessun problema di mappatura) |
| 2 Parser di Player.log | **codice e test su log sintetici fatti; da verificare sui log reali** (almeno un draft Premier e uno Pick Two) |
| 3-9 | da fare |

Cose aperte:
- **Log di Arena:** l'utente li sta recuperando. Quando arrivano: pulirli con
  `python -m draftbot log-clean <log> tests/fixtures/logs/<nome>.log --redact "<nome utente>"`,
  rileggere a mano l'output, poi `log-drafts <fixture> -d`, e aggiungere test di regressione sui
  fixture reali. Vedi la checklist in `docs/phase2_report.md`. Mai committare il log grezzo.
- **Lettore del database del client Arena** (`draftbot/sources/arena_db.py`): non verificato su
  un'installazione reale. Chiedere all'utente di provare
  `python -m draftbot update-cards TRK --arena-db "<cartella Raw>"`.
- **Carte ospiti:** la rarità è quella della stampa originale, non quella SPG. Va gestita nei prior
  per rarità (Fase 3).

Opzioni proposte all'utente per proseguire: (a) Fase 2 sulla struttura documentata, marcata come
non verificata, più lo script di pulizia; (b) Fase 3 (statistiche su FRA) mentre arrivano i log.
L'utente ha scelto (a): fatta. Prossimo passo: verifica sui log reali, poi approvazione e Fase 3.

## Comandi

```bash
pip install -e .[dev]          # dipendenze: PyYAML, pytest
python -m pytest               # i test usano una cartella dati temporanea
python -m draftbot add-set TRK # ricrea la cache locale (data/ non è versionata)
python -m draftbot add-set FRA
python -m draftbot status TRK
python -m draftbot cards TRK --mv 2 --color W
```

## Ambiente cloud

L'ambiente "mtga-draft-support" consente solo `api.scryfall.com`, `data.scryfall.io`,
`www.17lands.com` e `data.17landscapes.com` (oltre ai gestori di pacchetti). La cartella `data/`
non è nel repository: in una sessione nuova va rigenerata con `add-set`.
