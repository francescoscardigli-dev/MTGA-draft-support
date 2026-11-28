# Fase 7 — Segnali, wheel, lettura live del log

Data: 2026-11-28. Sviluppo e verifica su **FRA**. I modelli sono stimati fino al 2026-10-13 e
verificati su 150 draft successivi di giocatori della fascia alta per formato. Output completi in
`docs/backtest/FRA_<formato>_phase7.txt`.

## In sintesi

| | Premier | Pick Two |
|---|---|---|
| Wheel: Brier del modello / probabilità costante | **0,174** / 0,222 | **0,202** / 0,240 |
| Accordo top-1: base → con wheel | 50,6% → **51,5%** | 36,4% → **37,0%** |
| Segnali: log-verosimiglianza dei colori finali, con − senza | **−0,041** (peggio) | **−0,007** (peggio) |

- **Il wheel è attivo**: è ben calibrato e migliora un po' i pick. Il guadagno è piccolo e non ne
  ho verificato la significatività con 150 draft.
- **I segnali, come li ho costruiti, non funzionano e sono disattivati.** Il codice e la verifica
  restano, per riprovarli con altre definizioni.
- **La lettura live** funziona sui log sintetici; sui log reali va verificata insieme alla Fase 2.

## Wheel (`draftbot/engine/wheel.py`)

Domanda: una carta della busta tornerà dopo un giro del tavolo? Il modello è parametrico rispetto
alla config del formato. Tra un giro e l'altro passano `pod_size − 1` giocatori, ognuno prende
`picks_per_turn` carte: 7 in Premier, 6 in Pick Two.

Ho provato due modelli, entrambi stimati per massima verosimiglianza sugli esiti reali. Nei draft
17Lands la busta vista al turno t si rivede al turno t + `pod_size`.

1. **Sopravvivenza in stile Plackett-Luce**: ogni giocatore prende una carta con probabilità
   proporzionale a un peso che dipende da qualità e popolarità. È risultato troppo "piatto" agli
   estremi (prevista 0,72, reale 0,84 in Pick Two). La qualità non aggiunge nulla alla popolarità.
2. **Regressione logistica** (tenuta), con tre variabili:
   - la posizione di popolarità della carta nella busta, rispetto al numero di carte che verranno
     prese (relativa e al quadrato);
   - la popolarità (1 − ATA normalizzata);
   - la dimensione della busta.

   Calibrazione in Premier (prevista → reale): 0,07 → 0,08; 0,19 → 0,18; 0,39 → 0,39; 0,59 → 0,60;
   0,76 → 0,80. In Pick Two il wheel è più incerto, perché non sappiamo cosa giocano gli altri tre.

**Uso nel pick:** al valore di un'opzione si aggiunge il valore atteso della carta migliore che
tornerà se si prende quell'opzione. Le carte rimaste si ordinano per valore; la i-esima conta con
probabilità w_i × Π_{j<i}(1 − w_j). Vale 0 se la busta non tornerà. In Pick Two si conta una sola
carta di ritorno (approssimazione).

## Segnali (`draftbot/engine/signals.py`)

Definizione provata:
- per ogni busta vista, escluso il primo pick di ogni busta, si misura per colore la qualità delle
  2 migliori carte;
- la si confronta con quella attesa allo stesso turno, stimata sui draft di stima;
- lo scarto si accumula per direzione di passaggio, presa dalla config del formato;
- entra nelle probabilità degli archetipi come termine g_same × segnale (stessa direzione della busta
  corrente) + g_other × segnale (altra direzione), con i pesi stimati per verosimiglianza sui colori finali.

**Risultato:** i pesi stimati sono negativi (Premier −4 e −8) e fuori campione la previsione dei
colori finali peggiora. Interpretazione: una volta noto il valore del pool, la qualità che arriva
in un colore non dice nulla in più su dove finirà il giocatore. In parte è anche perché i giocatori
forti stessi non seguono un segnale definito così. Nel modello salvato i pesi sono 0
(`priors/<formato>_extras.yaml`), e `fit-extras` li riattiva in automatico solo se migliorano fuori
campione.

Strade non provate, da considerare:
- segnali dalle **carte mancanti** nella busta di ritorno, cioè cosa hanno preso gli altri: è
  un'informazione più diretta della qualità rimasta;
- segnali solo dalle **carte forti** che arrivano tardi rispetto alla loro ATA.

## Lettura live (`draftbot/live.py`)

`python -m draftbot live [--log Player.log] [--set TRK] [--format premier]`

- `LogTailer` segue il file e gestisce le righe incomplete e la rotazione: al riavvio di Arena il
  file si accorcia e si riparte dall'inizio.
- `LiveSession` usa il parser incrementale della Fase 2. Per ogni nuova busta del draft in corso
  produce un evento `pick` con:
  - le k opzioni con valore, guadagno sul mazzo e bonus di wheel;
  - le carte della busta con qualità e probabilità di ritorno;
  - le probabilità degli archetipi e il pool;
  - gli ID senza corrispondenza nel database.

  A draft finito produce un evento `build` con le 3 build.
- Set e formato si leggono dal nome dell'evento; se non si riconoscono, lo si dice e si possono
  forzare con `--set` e `--format`.
- Gli eventi sono dizionari JSON: la UI della Fase 8 li riceve dallo stesso oggetto.
- Anche `recommend --log` usa il wheel.

## Esempio di output

Log sintetico con ID Arena TRK, interrotto alla busta del P1p4 (`live --once` legge il file ed esce):
```
$ python -m draftbot live --log trk_live.log --once
TRK premier P1p4 (pool 3; BG 48%, WG 12%, UG 12%)
  1. Temporal Conqueror  +0.58 punti, wheel +0.00
  2. Subcommander T'Pol  +0.20 punti, wheel +0.15
  3. Starfleet Academy  +0.17 punti, wheel +0.17
  probabilita' di ritorno: Temporal Conqueror 35%, Subcommander T'Pol 7%, Breeding Pool 11%, Live Long and Prosper 40%, The Doctor, EMH 15%, Hallowed Fountain 18%
```
"wheel" è il valore atteso di ciò che tornerà al pick 12 se si prende quell'opzione: prendendo
Subcommander T'Pol, Temporal Conqueror ha il 35% di tornare. Per TRK la qualità viene dal solo prior
(nessun game data).

## Limiti

- La lettura live non è verificata su un vero Player.log: dipende dalla verifica della Fase 2.
- Per TRK la popolarità viene dai draft data TRK (già pubblici), il modello di wheel da FRA.
- Il wheel in Pick Two conta una sola carta di ritorno.
- Segnali disattivati (vedi sopra).

## Test

`tests/test_live.py`:
- la sopravvivenza diminuisce con il peso e con i passaggi;
- il modello logistico impara la popolarità;
- i segnali si accumulano per direzione e il primo pick viene ignorato;
- il tailer gestisce righe incomplete e rotazione;
- su un log sintetico, letto riga per riga, la sessione live produce un evento per ognuno dei
  42 pick e una build finale.
