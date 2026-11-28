# Fase 4 — Valutatore di mazzo, motore di pick, backtest

Data: 2026-11-28. Sviluppo e verifica su **FRA**. Tutto ciò che il backtest usa è stimato con i dati
fino al **2026-10-13** (prior sui set precedenti HOB e MSH). La verifica è fatta sui draft
successivi dei giocatori della fascia alta (win rate ≥ 58%).

## Come funziona

Pacchetto `draftbot/engine/`:

| File | Contenuto |
|---|---|
| `book.py` | catalogo delle carte con la qualità `q` (GIH WR bayesiana della Fase 3 meno la media, in punti di win rate) |
| `deckmodel.py` | **valutatore di mazzo**: win rate attesa del mazzo, stimata dai game data |
| `projection.py` | mazzo finale proiettato a metà draft e probabilità degli archetipi |
| `pick.py` | `recommend(pool, pack, k)`: le k opzioni migliori; in Pick Two le coppie |
| `backtest.py` | lettura dei draft 17Lands, accordo con i giocatori forti, verifiche dei mazzi |

### Valutatore di mazzo

Regressione della win rate del mazzo (per partita) su qualità media delle carte, qualità delle 3
migliori, forza dell'archetipo, carte di costo ≤ 2 e ≥ 5, terre, splash e colori in più. Il
**livello del giocatore è una variabile di controllo**: chi gioca bene costruisce anche mazzi
migliori, e senza controllo l'effetto del mazzo verrebbe sovrastimato. Il livello del giocatore non
entra nel punteggio.

Coefficienti su FRA Premier (dati fino al taglio):

| Caratteristica | Effetto sulla win rate del mazzo |
|---|---|
| qualità media (+1 punto) | +1,56 punti |
| qualità delle 3 migliori (+1 punto) | +1,37 punti |
| splash | −3,2 punti |
| ogni colore principale oltre 2 | −3,6 punti |
| ogni terra oltre 17 | −0,9 punti |
| curva (carte a costo ≤ 2 e ≥ 5) | circa 0 |
| forza dell'archetipo | fissata a 0 (vedi sotto) |

- **La forza dell'archetipo è già nella qualità delle carte.** Stimata insieme alla qualità, esce con
  coefficiente negativo, perché le carte degli archetipi forti hanno già una GIH WR alta. Un termine
  negativo allontanerebbe il motore dagli archetipi forti, quindi viene fissato a 0 e la cosa è
  scritta nelle note del modello.
- **La curva conta poco** una volta che i mazzi sono ragionevoli. Nell'esplorazione i mazzi molto
  aggressivi di FRA andavano un po' meglio (+2,6 punti con 15+ carte a costo ≤ 2), ma solo agli estremi.

### Valore di un pick

    valore(o) = somma su a di  P(a | pool) × [E_a(pool + o) − E_a(pool)]

- E_a è il valore del **mazzo finale proiettato** nell'archetipo a: le 23 carte migliori tra quelle
  del pool giocabili in a, quelle attese dai pick futuri e delle carte di riempimento.
  - Pick futuri: pick rimasti × quota che finisce nel mazzo, con la qualità del profilo di un mazzo
    tipico dei giocatori forti.
  - Riempimento: qualità dell'ultimo posto del profilo. Una carta peggiore del riempimento non si gioca.
- Il **valore di rimpiazzo è implicito**: una carta vale solo per quanto migliora le 23 carte finali.
  A inizio draft deve battere le carte che arriveranno; a draft avanzato, quelle già prese.
- **Flessibilità**: una carta monocolore è giocabile in 4 archetipi su 10, una multicolore in 1. A
  parità di qualità, all'inizio il motore preferisce la monocolore senza una regola scritta a mano.
- **Pick Two**: si valutano tutte le coppie insieme (91 con 14 carte). Due carte che competono per
  lo stesso posto nel mazzo non si sommano.
- **Probabilità degli archetipi**: `P(a) = (1 − ε) · softmax(log quota_a + λ(t) · E_a) + ε · quota_a`.
  λ(t) ed ε(t) sono stimati per massima verosimiglianza sui colori finali dei mazzi reali (draft
  data uniti ai game data con il `draft_id`), per decimo di avanzamento del draft. ε è la quota di
  giocatori che "cambiano strada" anche con un pool chiaro. In Premier λ scende da 780 a 140 ed ε dal
  20% a 0. λ del primo decile tocca il limite della griglia (800); una griglia più ampia non cambiava la
  verosimiglianza fuori campione.
- **Spareggio**: a fine busta quasi nessuna carta entra nel mazzo proiettato e le opzioni valgono
  tutte circa 0. Ho provato un termine secondario (qualità nei colori probabili), con il peso stimato
  sui draft prima del taglio: **non migliora l'accordo in nessuno dei due formati**, quindi il peso è 0.

### Calibrazione delle probabilità degli archetipi (FRA Premier, draft dal 20/10)

Probabilità dell'archetipo più probabile e frequenza con cui il mazzo finale è davvero in
quell'archetipo:

| Carte nel pool | Prob. 0,3–0,6 | Prob. 0,6–0,9 | Prob. 0,9–1 |
|---|---|---|---|
| 1–3 | 0,40 → 0,45 | **0,76 → 0,58** | — |
| 4–7 | 0,46 → 0,54 | 0,76 → 0,66 | — |
| 8–12 | 0,47 → 0,52 | 0,77 → 0,71 | 0,91 → 0,75 |
| 13–42 | 0,48 → 0,48 | 0,78 → 0,71 | 0,94 → 0,92 |

La prima versione (un solo λ, senza ε) era molto più sicura del dovuto: 97% previsto contro circa
75% reale. Con λ(t) ed ε(t) la calibrazione è buona a draft avanzato. **Resta una sovrastima nei primi
pick** (76% previsto contro 58% reale con 1–3 carte). Ho provato a comprimere le differenze grandi di
valore (saturazione): non migliora la verosimiglianza, quindi l'ho tolta. Il pool dei primi pick spiega
i colori finali meno di quanto il modello creda, perché contano le buste successive (segnali, Fase 7).

## Backtest (FRA, 300 draft di verifica per formato)

Output completi in `docs/backtest/FRA_premier.txt` e `FRA_pick_two.txt`. Riferimenti: "qualità" è la
carta di `q` più alta senza guardare il pool; "ATA" è la carta presa in media più presto da tutti i
giocatori, cioè la scelta della maggioranza.

### Accordo con i giocatori forti

| | Premier top-1 | Premier top-3 | Pick Two top-1 (coppia esatta) | Pick Two top-3 | Pick Two carte in comune |
|---|---|---|---|---|---|
| **Motore** | **50,8%** | **81,2%** | **36,4%** | **57,4%** | **61,5%** |
| ATA | 46,0% | 80,7% | 33,8% | 56,5% | 59,1% |
| Sola qualità | 40,0% | 78,4% | 30,5% | 50,5% | 56,0% |

Differenza di top-1 del motore, con intervallo al 95% (bootstrap sui draft, perché i pick dello
stesso draft non sono indipendenti):

| | contro ATA | contro sola qualità |
|---|---|---|
| Premier | **+4,9 punti** [+3,7, +6,1] | +10,9 [+9,9, +11,9] |
| Pick Two | **+2,6 punti** [+1,2, +4,0] | +5,9 [+4,7, +7,1] |

Per fase della busta (top-1):

| | inizio | metà | fine |
|---|---|---|---|
| Premier: motore / ATA | **44%** / 38% | **45%** / 34% | 67% / **70%** |
| Pick Two: motore / ATA | **23%** / 17% | **25%** / 21% | 68% / **71%** |

- Il guadagno sulla sola qualità viene dal pool: colori, rimpiazzo, flessibilità.
- Il motore batte ATA dove le scelte contano (inizio e metà busta). A fine busta ATA resta un po'
  meglio: lì i giocatori forti scelgono carte che non entreranno nel mazzo e seguono gusti che la
  win rate non vede. La posta di quei pick è bassa.
- L'accordo misura somiglianza ai giocatori forti, non la qualità delle scelte: un disaccordo
  non è per forza un errore del motore.

### Valutatore di mazzo fuori campione

| | Mazzi dopo il taglio | Pendenza win rate reale / punteggio (atteso 1) |
|---|---|---|
| Premier | 39.094 | **0,93 ± 0,02** |
| Pick Two | 20.468 | **0,98 ± 0,03** |

Il punteggio del mazzo prevede la win rate reale nella misura giusta (in Premier sovrastima di
circa il 7% le differenze). La correlazione col risultato del singolo mazzo è bassa (circa 0,12),
ed è normale: un mazzo gioca in media 4 partite.

### Mazzo proposto contro mazzo reale

Dal pool finale, il mazzo semplice del motore (le 23 carte migliori nella coppia di colori
migliore) contro quello costruito dal giocatore, con lo stesso valutatore:

| | Draft | Differenza media | Proposto ≥ reale | Stessa coppia di colori |
|---|---|---|---|---|
| Premier | 252 | +1,1 punti | 77% | 73% |
| Pick Two | 203 | +1,8 punti | 82% | 62% |

**Attenzione:** è un confronto a favore del motore, perché il mazzo proposto massimizza proprio il
valutatore con cui viene giudicato. Indica che il valutatore e la scelta dei colori sono coerenti,
non che il mazzo proposto vinca di più. La costruzione vera (curva, terre, splash, 2-3 build) è la Fase 6.

## Modelli salvati (per l'uso)

`priors/<formato>_deck.yaml`, stimati su FRA, HOB e MSH insieme (λ sui draft FRA). Per TRK, che non
ha game data, si usano questi coefficienti con la qualità delle carte presa dal solo prior.

## Esempi di output

Pool vuoto (FRA Premier):
```
$ python -m draftbot recommend FRA --format premier --pack "Ajani Unrelenting;Unsummon;Bestial Incursion;Violent Echoes;Woodwork Prodigy;Mind Twist"
Pool: 0 carte. Archetipi piu' probabili: UB 19%, WU 16%, RG 12%, WG 11%
1. Ajani Unrelenting  ->  +2.03 punti di win rate attesa
   Ajani Unrelenting q +13.6; guadagno sul mazzo +2.03; per archetipo: RG 0.7, UR 0.6, BR 0.5
2. Mind Twist  ->  +1.08 punti di win rate attesa
```

Stessa busta dopo tre carte UB forti. Qui si vede il limite descritto sopra:
```
Pool: 3 carte. Archetipi piu' probabili: UB 84%, WU 3%, RG 2%, WG 2%
1. Mind Twist  ->  +1.43 punti di win rate attesa
2. Ajani Unrelenting  ->  +0.39 punti di win rate attesa
3. Unsummon  ->  +0.21 punti di win rate attesa
```
Molti giocatori forti prenderebbero comunque Ajani. Con il 58% reale al posto dell'84% il confronto
sarebbe più vicino. È il caso da riguardare con i segnali (Fase 7).

Pick Two da Player.log (log sintetico con ID Arena TRK; per TRK la qualità viene dal solo prior):
```
$ python -m draftbot recommend TRK --format pick_two --log trk_pick_two.log
Dal log: P2p3 del draft d0d0d0d0-...
Pool: 18 carte. Archetipi piu' probabili: WU 36%, WB 31%, WG 18%, WR 9%
1. Lieutenant Commander Data + Tasha Yar, Out of Time  ->  +0.44 punti di win rate attesa
2. Tasha Yar, Out of Time + The Caretaker  ->  +0.43 punti di win rate attesa
3. Tasha Yar, Out of Time + Fugitive Listener  ->  +0.38 punti di win rate attesa
```

## Limiti

- **Coppie di colori soltanto:** gli archetipi sono i 10 a due colori; tre colori e splash entrano
  solo nel valutatore di mazzo (penalità stimate), non nel pick. Fase 6.
- **La qualità delle carte è la stessa in tutti gli archetipi.** La Fase 3 ha mostrato che la
  differenza per archetipo è piccola con questi volumi. Le sinergie vere sono la Fase 5.
- **Segnali e wheel non ci sono ancora** (Fase 7): il motore non guarda cosa passa dai vicini.
- **Impegno sui colori troppo precoce** nei primi pick (vedi calibrazione ed esempio).
- Curva e terre non entrano nella proiezione a metà draft (non si sanno prima del mazzo finale).
- In Pick Two il "top-1" richiede la coppia esatta tra 91: per questo è più basso che in Premier.
- Bug trovati e corretti durante il lavoro:
  - 21 carte FRA (nomi con faccia frontale in 17Lands e `A // B` nel catalogo) venivano lette come
    terre di valore 0 nel motore; ora i nomi si mappano come in Fase 3;
  - una carta pessima poteva avere valore negativo;
  - lambda salvato in un formato non scrivibile in YAML.

## Test

`tests/test_engine.py` (offline, catalogo finto): carta più forte a pool vuoto, preferenza per la
monocolore a parità di qualità, carta nei colori con pool impegnato, carta sotto il rimpiazzo che vale
0, carta pessima mai negativa, coppie in Pick Two, carte future che diminuiscono nel draft, stima di
λ su dati simulati, mazzo proposto nella coppia giusta, salvataggio del modello, limite della miscela.
Totale suite: 64 test.
