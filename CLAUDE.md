# Contesto per Claude Code

Assistente locale (Python 3.11+) per i draft di MTG Arena: legge `Player.log`, suggerisce i pick
spiegando il punteggio, aiuta col mazzo finale. Formati: solo Premier Draft e Pick Two Draft.
Set di test: TRK (Star Trek, con il foglio bonus SDS). Set per il test di generalità e lo sviluppo
delle statistiche: FRA.

Documenti da leggere prima di lavorare: `docs/plan.md` (piano approvato),
`docs/phase0_report.md` (verifiche), `docs/phase1_report.md` (database carte),
`docs/phase2_report.md` (parser del log), `docs/phase3_report.md` (statistiche),
`docs/phase4_report.md` (motore di pick e backtest), `docs/phase5_report.md` (sinergie),
`docs/phase6_report.md` (costruzione del mazzo), `docs/phase7_report.md` (wheel, segnali, live), `docs/phase8_report.md` (UI), `docs/phase9_report.md` (generalità, cose aperte), `docs/phase10_report.md` (imitazione, fixing, splash).
Guida d'uso: `docs/guida.md`.

## Modalità di lavoro richiesta dall'utente

- Si lavora in italiano.
- Si procede fase per fase. A fine fase: eseguire i test, mostrare un esempio di output e
  **fermarsi per l'approvazione** prima della fase successiva.
- Se un dato o una fonte non è disponibile, dirlo invece di inventarlo.
- Nessun dato specifico di un set nel codice del motore (c'è un test che lo controlla): tutto
  sta in `sets/<CODICE>.yaml` e `formats/<nome>.yaml`.
- Branch di lavoro: quello assegnato alla sessione. Fasi 0-1 su `claude/mtg-draft-tool-review-kse14y`,
  Fasi 2-6 (e seguenti) su `claude/blissful-tesla-2atj16` (che contiene anche le fasi precedenti).

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

## Stato al 2026-11-28 (tutte le fasi del piano completate)

| Fase | Stato |
|---|---|
| 0 Verifiche e scheletro | completata |
| 1 Database carte, ID Arena, `add-set` | completata (TRK 341 carte, FRA 295, nessun problema di mappatura) |
| 2 Parser di Player.log | **verificato su log reali Premier e Pick Two TRK** (macOS, Steam; fixture in `tests/fixtures/logs/`) |
| 3 Statistiche 17Lands | completata (sviluppo e calibrazione su FRA, prior da FRA+HOB+MSH) |
| 4 Valutatore di mazzo, pick, backtest | completata |
| 5 Sinergie | completata: misurate, ma fuori dal punteggio di default (peggiorano il backtest) |
| 6 Costruzione del mazzo | completata |
| 7 Segnali, wheel, live | completata: wheel attivo; flusso dei colori attivo (revisione su indicazione dell'utente); impegno ridotto provato, nessun vantaggio |
| 8 UI web locale, revisione | completata (verificata su log sintetici) |
| 9 Generalità, rifinitura | completata (test su OM1 ed EOE da zero; 3 problemi di generalità corretti) |
| 10 Idee da altri tool | strato di imitazione (+3/+9 top-1), fixing nel valutatore e al pick, splash dai dati: vedi `docs/phase10_report.md` |

Cose aperte (aggiornato 2026-11-28, sessione del Pick Two), in ordine:
1. **Log reali: verificati entrambi i formati.** Premier TRK (`tests/fixtures/logs/premier_trk_real.log`,
   `test_real_premier_log`) e Pick Two TRK (`pick_two_trk_real.log`, `test_real_pick_two_log`): il parser
   legge 21/21 pick senza note. Per nuovi log: `log-clean <log> tests/fixtures/logs/<nome>.log` (il nome
   utente non compare nei log di macOS/Steam visti finora), controllo a mano, test. Mai committare il log grezzo.
2. **UI dal vivo, primo draft Pick Two dell'utente (2026-11-28): la pagina restava vuota.** Riprodotto qui:
   parser e motore funzionano riga per riga; la causa piu' probabile e' il primo caricamento del motore per
   un formato nuovo (cache di `signal_baseline`: 17-19 s qui, di piu' sul MacBook 2015), fatto solo alla prima
   busta e senza alcun messaggio, con il timer di Arena sotto il minuto. Corretto: con `--set/--format` il
   motore si carica all'avvio (`LiveSession.preload`); messaggi di stato nel Terminale e nella pagina
   (caricamento, motore pronto, log seguito o non trovato, draft trovato); senza `--log` la UI legge prima
   `Player-prev.log`. **Da confermare sul Mac dell'utente al prossimo draft** (se resta vuota: chiedere le
   righe stampate nel Terminale). Durante il draft l'utente ha usato i consigli via screenshot in chat.
3. **Installazione dell'utente:** macOS Monterey, MacBook Pro 2015 Intel, Python 3.14 da python.org, Arena
   da Steam; codice scaricato come ZIP del branch in `~/Documents/MTGA-draft-support`, venv `.venv`.
   Funzionano `add-set`, `update-data`, `status`, `ui` (dopo `pip install websockets`, ora nelle dipendenze
   `[ui]`). Ancora da provare dall'utente: `build --log` sul suo draft. Ricordargli: Control+C (non Cmd+C)
   per fermare il server; la pagina non si apre da sola. Per avere le correzioni deve riscaricare lo ZIP.
4. **Database del client Arena** (`update-cards --arena-db`): non verificato; con Arena da Steam su macOS la
   cartella `Raw` non e' quella standard ("Database del client Arena non trovato"). Da cercare con l'utente.
5. **Pick Two:** il formato piu' difficile (top-1 40-43% con lo strato di imitazione, Premier 56-64%).
6. **Fixing nei primi pick:** il motore da solo prende poche terre a inizio busta; lo strato di imitazione
   corregge in gran parte (Fase 10).
7. **Splash:** ora 13-25% dei mazzi proposti (reale 28-47%); i dati dicono che costa 2-3 punti a parita' di
   qualita' (Fase 10). Le build "2 colori + splash" hanno sostituito parte di quelle a 3 colori.
8. **TRK:** qualita' dal solo prior finche' 17Lands non pubblica i game data; allora `update-data TRK`,
   ricontrollare calibrazione e, se serve, ristimare i modelli (`fit-deck-model`, `fit-imitation`).
9. **Tag dei temi** (`sets/*.tags.yaml`): generati con regole, `reviewed: false`, da rivedere a mano.
10. Minori: rarita' delle carte ospiti (stampa originale, effetto piccolo); statistiche per archetipo non
    usate (tau circa 0,3).

Note tecniche da ricordare:
- Set con buste diverse: `format_overrides` in `sets/<CODICE>.yaml` (EOE: Premier 13 carte); usare sempre
  `format_for(formato, set)`, non `load_format`, dove il set e' noto.
- Modelli generici in `priors/`: prior, mazzo (con fixing e splash), extras (wheel, flusso), imitazione.
  Rigenerazione: vedi `docs/guida.md` sezione 7 e `docs/phase10_report.md`.
- Le cache delle verifiche dello strato di imitazione stanno in `data/imitation/` (non versionate).

Opzioni proposte all'utente per proseguire: (a) Fase 2 sulla struttura documentata, marcata come
non verificata, più lo script di pulizia; (b) Fase 3 (statistiche su FRA) mentre arrivano i log.
L'utente ha scelto (a): fatta. Poi ha chiesto le Fasi 3 e 4, poi di procedere in autonomia con 5-9:
fatte. Elenco ordinato delle cose aperte in `docs/phase9_report.md`; la prima è la verifica sui log
reali di Arena (Fase 2), che sblocca anche la verifica di live e UI.

## Comandi

```bash
pip install -e .[dev]          # dipendenze: PyYAML, numpy, pandas; dev: pytest, fastapi, httpx
python -m pytest               # i test usano una cartella dati temporanea
python -m draftbot add-set TRK # ricrea la cache locale (data/ non è versionata)
python -m draftbot add-set FRA
python -m draftbot add-set HOB && python -m draftbot add-set MSH   # set dei prior
python -m draftbot update-data FRA             # anche HOB, MSH, TRK (circa 10 minuti in totale)
python -m draftbot stats FRA --format premier
python -m draftbot recommend FRA --format premier --pack "..." --pool "..."
python -m draftbot status TRK
python -m draftbot cards TRK --mv 2 --color W
python -m draftbot ui                          # UI web locale (pip install -e .[ui])
# set nuovi del test di generalita': OM1 (Pick Two), EOE (Premier, buste da 13)
```

## Ambiente cloud

L'ambiente "mtga-draft-support" consente solo `api.scryfall.com`, `data.scryfall.io`,
`www.17lands.com` e `data.17landscapes.com` (oltre ai gestori di pacchetti). La cartella `data/`
non è nel repository: in una sessione nuova va rigenerata con `add-set` e `update-data`.
