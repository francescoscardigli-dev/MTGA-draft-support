# Fase 3 — Statistiche 17Lands, blend bayesiano, prior

Data: 2026-11-27. Sviluppo e calibrazione su **FRA**. Prior stimati sui set precedenti **HOB** e
**MSH** (e FRA per il prior salvato). Tutte le statistiche sono calcolate **in proprio** dai
dataset pubblici 17Lands (CC BY 4.0); nessun endpoint del sito.

## Dati usati

| Set | Formato | Partite (game data) | Pick (draft data) | Periodo |
|---|---|---|---|---|
| FRA | Premier | 431.627 | 3.430.369 | 2026-09-29 → 2026-11-01 |
| FRA | Pick Two | 125.652 | 777.357 | 2026-09-29 → 2026-11-01 |
| HOB | Premier | 323.114 | 2.436.691 | 2026-08-11 → 2026-09-22 |
| HOB | Pick Two | 171.956 | 1.046.219 | 2026-08-11 → 2026-09-22 |
| MSH | Premier | 377.514 | 2.882.562 | 2026-06-23 → 2026-07-24 |
| MSH | Pick Two | 106.417 | 647.589 | 2026-06-23 → 2026-07-24 |
| TRK | Premier | — (non pubblicati) | 998.949 | 2026-11-10 → 2026-11-22 |
| TRK | Pick Two | — (non pubblicati) | 259.534 | 2026-11-10 → 2026-11-22 |

`add-set HOB` e `add-set MSH` hanno creato le config da zero senza modifiche al motore
(193 e 339 carte, nessun problema di mappatura): secondo e terzo test di generalità dopo FRA.
Tutti i nomi delle carte 17Lands corrispondono al database carte.

## Cosa fa

`python -m draftbot update-data <SET>` scarica i file (cache in `data/raw/17lands/`, riscaricati
solo se 17Lands li aggiorna) e salva nel database **aggregati** per carta × colori del mazzo ×
giorno × fascia del giocatore, legati a uno **snapshot datato** (tabella `snapshots`). Dal giorno
si ricavano senza rileggere i file:
- il filtro `--as-of` (solo dati fino a un giorno: servirà per il backtest della Fase 4);
- il decadimento temporale (`--half-life`).

Metriche (definizioni di 17Lands): GIH WR, OH WR, GD WR, GP WR, GNS WR, IWD dai game data;
ATA, ALSA e taken rate dai draft data. ALSA: la stessa busta torna dopo `pod_size` turni (dalla
config del formato), quindi le copie presenti al turno t e sparite al turno t + `pod_size` sono
state viste l'ultima volta al turno t. Funziona uguale per Premier (8) e Pick Two (4).

Fasce del giocatore (dalla win rate dichiarata da 17Lands): `low` < 50%, `mid` 50–58%, `high` ≥ 58%.

Comandi: `update-data`, `stats`, `fit-prior`, `calibrate` (esempi nel README).

## Modello

1. **Prior della GIH WR** (`priors/<formato>.yaml`, versionato). Regressione ridge pesata della
   GIH WR, come scarto dalla media del set, su ATA, ATA², ALSA e taken rate (normalizzati sui turni
   del formato) più la rarità. Non usa game data del set da valutare: per TRK funziona già oggi.
2. **Blend bayesiano** (beta-binomiale): `stima = (vittorie + m·prior) / (partite + m)`, con
   `m = p0(1−p0)/τ²`. τ² (quanto le carte si discostano davvero dal prior) è stimato dai dati del
   set (empirical Bayes) oppure, se le carte con abbastanza partite sono poche, preso dal prior.
3. **Decadimento temporale**: le partite pesano `0,5^(età/emivita)`. Emivita consigliata salvata nel
   prior: 7 giorni per Premier, 14 per Pick Two.
4. **Incertezza**: `±` nel comando `stats` è l'incertezza sulla win rate dei prossimi giorni, cioè
   l'errore della stima più una **deriva** del formato (le win rate cambiano nel tempo), stimata sui
   set precedenti: circa 1 punto in entrambi i formati.
5. **Per archetipo** (`--colors`): il prior è la stima della carta su tutti i mazzi, spostata della
   differenza tra la GIH WR media dell'archetipo e quella complessiva.

## Calibrazione

Output completi in `docs/calibration/FRA_premier.txt` e `FRA_pick_two.txt` (riproducibili con
`calibrate FRA --format <f> --prior-sets HOB MSH`). Errore in punti percentuali, corretto per il
rumore del periodo di verifica; carte con almeno 300 partite.

### 1. Il prior, su un set escluso dalla stima

| Set escluso | Formato | Media del set | Solo rarità | Pick | Pick + rarità (corr.) |
|---|---|---|---|---|---|
| FRA | Premier | 2,66 | 2,90 | 1,26 | **1,20** (0,90) |
| HOB | Premier | 2,70 | 3,07 | 1,51 | **1,40** (0,85) |
| MSH | Premier | 3,00 | 3,21 | 1,56 | **1,53** (0,84) |
| FRA | Pick Two | 2,67 | 2,82 | 1,40 | **1,37** (0,86) |
| HOB | Pick Two | 2,73 | 3,01 | 1,34 | **1,28** (0,85) |
| MSH | Pick Two | 3,03 | 3,13 | 1,58 | 1,62 (0,83) |

Come i giocatori scelgono le carte predice la loro win rate con correlazione 0,83–0,90 e dimezza
l'errore rispetto alla media. La rarità da sola non aiuta: è utile solo insieme ai dati di pick
(a parità di ATA, una rara presa presto vale un po' meno di una comune presa presto).

### 2. Previsione dei giorni successivi (FRA, prior stimato solo su HOB e MSH)

Con i dati dei primi k giorni si prevede la GIH WR dei giorni successivi.

| Formato | Primi giorni | Dato grezzo | Solo prior | Blend verso la media | **Blend verso il prior** | Copertura 80% |
|---|---|---|---|---|---|---|
| Premier | 2 | 1,55 | 1,98 | 1,47 | **1,39** | 78% |
| Premier | 4 | 1,38 | 1,77 | 1,36 | **1,32** | 76% |
| Premier | 7 | 1,20 | 1,66 | 1,19 | **1,17** | 83% |
| Premier | 14 | 1,03 | 1,63 | **1,01** | 1,02 | 88% |
| Pick Two | 2 | 2,83 | 2,20 | 2,32 | **2,03** | 62% |
| Pick Two | 4 | 1,71 | 1,91 | 1,55 | **1,37** | 83% |
| Pick Two | 7 | 1,28 | 1,66 | 1,22 | **1,12** | 86% |
| Pick Two | 14 | 0,92 | 1,47 | 0,92 | **0,87** | 86% |

- Il blend verso il prior è il migliore o alla pari in tutti i casi. Il guadagno è maggiore quando
  i dati sono pochi: Pick Two, primi 2 giorni, 2,03 contro 2,83 del dato grezzo.
- Con più di due settimane di dati Premier il prior non aggiunge più nulla: decide il dato.
- **Intervalli:** senza la deriva gli intervalli all'80% coprivano solo il 50–63% in Premier
  (troppo stretti). Con la deriva stimata su HOB e MSH coprono il 76–88%. Eccezione: Pick Two nei
  primi 2 giorni (62%), con meno di 8.000 partite in tutto.

### 3. Decadimento temporale (scelta dell'emivita)

Errore sui giorni successivi con diverse emivite, due tagli per set:

| Formato | Esito |
|---|---|
| Premier | 7 giorni è la migliore in **6 casi su 6** (HOB, MSH, FRA). Su FRA: 1,19 → 0,98. |
| Pick Two | Misto: il decadimento aiuta su HOB e MSH, peggiora su FRA (meno partite: pesare meno i dati vecchi costa varianza). In media è migliore 14 giorni; su FRA costa da 0,05 a 0,13 punti. |

## Esempio di output

```
$ python -m draftbot stats TRK --format pick_two --limit 5
TRK Pick Two Draft, emivita 14 giorni (partite pesate)
Dati dal 2026-11-10 al 2026-11-22; partite: 0; GIH WR media:  57.4%; prior: modello FRA+HOB+MSH; tau: 1.71 punti (modello dei prior (dati del set insufficienti))
Nessun game data: la stima e' il solo prior (da ATA, ALSA e rarita').
carta                               stima    +-    GIH   n GIH     OH    GNS    IWD   ATA  ALSA  prior
Kenrith, the Returned King          66.2%   1.9    -         0    -      -      -    1.27  1.25  66.2%
U.S.S. Enterprise-D, Galaxy-Class   66.2%   1.9    -         0    -      -      -    1.10  1.10  66.2%
Excalbian Gamemaster                65.1%   1.9    -         0    -      -      -    1.24  1.26  65.1%
The Wrath of Khan                   65.1%   1.9    -         0    -      -      -    1.44  1.44  65.1%
Captain Jean-Luc Picard             64.7%   1.9    -         0    -      -      -    1.11  1.13  64.7%
```

```
$ python -m draftbot stats FRA --format premier --limit 4
FRA Premier Draft, emivita 7 giorni (partite pesate)
Dati dal 2026-09-29 al 2026-11-01; partite: 92539; GIH WR media:  56.1%; prior: modello FRA+HOB+MSH; tau: 1.21 punti (...)
carta                               stima    +-    GIH   n GIH     OH    GNS    IWD   ATA  ALSA  prior
Ajani Unrelenting                   69.6%   1.3  72.5%    1459  71.2%  52.7%  19.8%  1.23  1.24  67.1%
Uldaros Theorix                     66.7%   1.3  69.4%    1372  65.6%  50.1%  19.3%  1.49  1.53  64.5%
The Theorist, Jace Beleren          66.2%   1.3  66.4%    1397  65.5%  51.3%  15.2%  1.17  1.18  66.1%
Overwrite the Multiverse            64.2%   1.3  64.6%    1386  61.5%  51.1%  13.5%  1.59  1.62  63.9%
```

Con l'emivita le partite sono pesate: le 92.539 "partite" sono l'equivalente pesato delle 431.627 reali.

## Limiti e note

- **Il prior dai pick misura l'opinione dei giocatori, non la forza della carta.** Funziona perché
  le due cose sono molto correlate, ma eredita gli errori collettivi: una carta sottovalutata da
  tutti avrà un prior basso. Con i game data il dato corregge l'errore in pochi giorni.
- **Media del set senza game data:** per TRK la GIH WR media è presa dai set di stima (57,4% in
  Pick Two, 56,9% in Premier). I valori assoluti di TRK sono quindi indicativi; l'ordine tra le carte è
  la parte affidabile.
- **Per archetipo:** con i volumi di FRA la differenza propria di una carta in un archetipo risulta
  piccola (τ ≈ 0,3 punti), quindi la stima per archetipo è quasi sempre la stima complessiva più la
  differenza dell'archetipo. Non è ancora verificato con una divisione temporale; si riprende con
  le sinergie (Fase 5).
- I coefficienti di ATA e ALSA sono molto correlati tra loro (si compensano): vanno letti insieme,
  non uno per uno. La ridge li tiene stabili.
- Fasce del giocatore: le soglie 50% e 58% sono una scelta, non calibrata; la fascia `high` servirà
  come "giocatori forti" nel backtest.
- Le carte ospiti hanno la rarità della stampa originale (vedi Fase 1). Nel prior la rarità pesa
  meno di 1 punto, quindi l'effetto è piccolo.
- Un avviso di pandas ha fatto emergere che alcuni `draft_id` esadecimali (es. `12e34...`) venivano
  letti come numeri: ora i metadati si leggono sempre come testo (c'è un test).

## Test

`tests/test_stats.py` (offline, dati sintetici con risultato noto):
- conteggi dei game data tra chunk diversi;
- ATA e ALSA con la busta che torna;
- limiti del blend (nessun dato → prior; molti dati → dato);
- recupero di τ noto;
- il prior recupera la relazione con l'ATA e batte la media;
- solo prior senza game data;
- filtri `as_of`, colori e decadimento;
- archetipi.

Totale suite: 53 test.
