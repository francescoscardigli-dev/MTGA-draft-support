# Fase 9 — Generalità, rifinitura, documentazione

Data: 2026-11-28.

## Test di generalità: due set mai visti, da zero

Su due set mai usati prima ho fatto girare l'intera catena, senza toccare il codice per il set:
`add-set`, `update-data`, `status`, `stats`, `tag-set`, `synergy`, `calibrate` (prior stimato su FRA,
HOB e MSH) e `backtest` (stima fino a metà dei dati, verifica dopo, giocatori della fascia alta).
Output completo in `docs/backtest/generalita_OM1_EOE.txt`.

- **OM1** (Through the Omenpaths): set solo per Arena, solo Pick Two.
- **EOE** (Edge of Eternities): Premier, con buste da **13 carte**.

### Problemi trovati e corretti

Il test ha fatto il suo lavoro: tre problemi di generalità che FRA, HOB, MSH e TRK non mostravano.

1. **Carte rinominate per Arena (OM1).** Su Scryfall queste carte hanno il nome originale in `name`
   (es. "Spider-Verse") e quello di Arena in `printed_name` ("Arachnomania"). Anche 17Lands
   (`cards.csv`) le elenca con il nome e l'espansione originali (SPM).
   - Prima: 79 carte importate su 232.
   - Correzione: indice anche su `printed_name` e `flavor_name` per le stampe dei set del draft; nome
     canonico = quello di Arena; ID Arena accettati anche con il nome originale.
   - Dopo: 227 carte. Ne restano 5 non trovate su Scryfall (leggende nuove di OM1, probabilmente
     non ancora nell'elenco del set) e 35 senza ID Arena; `status` e `issues` le elencano.
2. **Buste di dimensione diversa (EOE: 13 carte in Premier, 39 pick).** Il formato Premier aveva 14
   carte fisse.
   - Prima: il backtest scartava tutti i draft e l'ALSA escludeva 313.131 righe.
   - Correzione:
     - `format_overrides` nella config del set (`premier: {pack_size: 13}`), letto da
       `format_for(formato, set)` in tutto il motore, parser del log compreso;
     - `add-set` rileva la dimensione delle buste dai draft data 17Lands e scrive l'eccezione da solo;
     - `update-data` avvisa se dati e config non tornano.
   - Dopo: 0 righe escluse.
3. **Formati senza dati in config (OM1 non ha Premier).** Ora `add-set` mette solo i formati con
   draft data pubblici (tutti, se il set è appena uscito e non ce n'è nessuno).

TRK e FRA, reimportati dopo le correzioni, sono invariati (341 carte e 450 ID; 295 e 431).

### Risultati

| | OM1 Pick Two | EOE Premier | (FRA, per confronto) |
|---|---|---|---|
| Statistiche: errore del blend verso il prior / dato grezzo, primi 7 giorni | 0,79 / 0,81 | 0,84 / 0,85 | 1,17 / 1,20 (Premier) |
| Copertura degli intervalli all'80% | 89% | 85% | 83% |
| **Pick, top-1 motore / ATA** | 35,2% / **36,6%** | **55,5%** / 46,5% | 50,8% / 46,0% |
| Differenza motore − ATA (intervallo al 95%) | −1,4 [−3,8, +0,8] | **+9,0 [+6,8, +11,2]** | +4,9 [+3,7, +6,1] |
| Valutatore di mazzo: pendenza fuori campione | 1,04 ± 0,03 | 0,97 ± 0,02 | 0,93 ± 0,02 |
| Sinergie: pendenza fuori campione | 0,29 | 0,03 | 0,36 |

- **Il valutatore di mazzo e le statistiche generalizzano bene**: pendenze vicine a 1 e intervalli
  calibrati su entrambi i set nuovi.
- **Il motore di pick batte nettamente la scelta media in Premier** (EOE +9 punti, FRA +5).
  **In Pick Two non la batte** (OM1 −1,4, non significativo; FRA +2,6). In Pick Two il vantaggio è
  piccolo o nullo: il formato a coppie, con soli 4 giocatori, è quello in cui il modello aiuta meno.
- **Le sinergie restano instabili** (pendenza 0,03 su EOE): confermata la scelta di tenerle fuori dal
  punteggio.
- Note su EOE:
  - il modello del mazzo stimato su EOE pesa la qualità media più che su FRA (+3,7 contro +1,6 punti
    per punto di qualità): i coefficienti dipendono dal set;
  - nel backtest su EOE il top-1 con le sinergie è 55,3% (senza 55,5%), cioè di nuovo nessun beneficio.

## Rifinitura

- **`status`** ora mostra anche cosa è disponibile per il motore, per ogni formato: game e draft data,
  prior, modello del mazzo, wheel, sinergie, e lo stato dei tag (generati o rivisti a mano).
- **README** riscritto in forma finale, con stato e rimandi; **guida d'uso** in `docs/guida.md`
  (installazione, dati, durante il draft, dopo il draft, come leggere i numeri, aggiungere un set,
  aggiornare i modelli).
- Test: 81, tutti verdi, offline.

## Cosa resta aperto

In ordine di importanza:

1. **Log reali di Arena.** Il parser (Fase 2), la lettura live (Fase 7) e la UI (Fase 8) sono
   verificati solo su log sintetici costruiti sulla struttura documentata da fonti non ufficiali.
   Checklist in `docs/phase2_report.md`: `log-clean`, poi `log-drafts -d`, poi i test sui fixture reali.
2. **Database del client Arena** (`update-cards --arena-db`): non provato su un'installazione reale.
3. **Impegno sui colori nei primi pick** (Fase 4). Riesaminato con l'utente (Fase 7, "Revisione"):
   - il flusso dei colori ora è nel modello: i colori aperti alzano il valore delle carte future;
   - impegnarsi meno nella busta 1 non migliora l'accordo con i giocatori forti;
   - i dati osservazionali suggeriscono che cambiare colori tardi costi (−3/−4 punti) e che 3 colori
     nei primi pick rendano un po' di più, ma senza una prova diretta.
4. **Pick Two**: il vantaggio sul riferimento ATA è piccolo (FRA) o nullo (OM1).
5. **Tag dei temi** (`sets/*.tags.yaml`): generati da regole, da rivedere a mano.
6. **Splash** molto più raro che nei mazzi dei giocatori forti (Fase 6).
7. **TRK**: quando 17Lands pubblica i game data, `update-data TRK` e poi `synergy TRK`. La
   calibrazione delle Fasi 3-4 andrebbe ripetuta su TRK.
