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

## Revisione (2026-11-28): segnali come flusso dei colori e impegno nei primi pick

Su indicazione dell'utente ho riesaminato due idee:
1. se le carte di un colore smettono di arrivare, i vicini lo stanno draftando; se ne arrivano tante,
   il colore è aperto e conviene andarci, anche cambiando a metà della busta 1;
2. nei primi pick conviene impegnarsi su un colore solo e tenere aperto il secondo, salvo carte
   eccezionali.

### 1. Il flusso dei colori esiste e dipende dalla direzione

Nuova definizione dell'offerta di un colore in una busta: somma, sulle carte non terra di quel colore,
della qualità sopra la soglia di carta giocabile (−2 punti). Il segnale è lo scarto medio dall'atteso,
per direzione di passaggio. Su 1.500 draft FRA per formato:

| Lo scarto nei pick 2-8 della busta 1 prevede l'offerta… | Premier | Pick Two |
|---|---|---|
| …nella busta 3 (stessi vicini) | **corr +0,20** [+0,17, +0,22] | **+0,17** [+0,14, +0,19] |
| …nella busta 2 (altra direzione: controllo) | +0,03 | +0,05 |
| …alla fine della busta 1 (buste di ritorno) | +0,38 | +0,29 |

La busta 2 che non risponde dimostra che è un effetto dei vicini e non statistico.

Perché la prova della Fase 7 era negativa: lì il segnale entrava solo nelle probabilità dei colori
**del giocatore**, cioè serviva a imitarlo. Il valore dei colori aperti sta invece nelle **carte future**.

**Nel modello:** le carte future attese di un archetipo si spostano di δ × segnale della direzione da
cui arriveranno le buste rimanenti (`signals.future_shift`). δ è stimato sui draft: di quanto il
segnale sposta la qualità del mazzo finale realmente ottenuto rispetto alla proiezione.
- verifica, stima fino al 13/10: Premier +0,151 ± 0,035, Pick Two +0,187 ± 0,034;
- modelli salvati, tutti i dati: Premier +0,167, Pick Two +0,138.

**Effetto sull'accordo con i giocatori forti** (300 draft dopo il taglio, wheel attivo in tutte le
varianti, intervallo appaiato sugli stessi draft):

| | Premier top-1 | Pick Two top-1 | Pick Two top-3 |
|---|---|---|---|
| base | 51,7% | 37,2% | 57,7% |
| + flusso | 51,9% (+0,1 [−0,2, +0,4]) | 37,4% (+0,3 [−0,2, +0,8]) | **58,7%** |

L'effetto è piccolo: lo spostamento tipico vale circa 0,15 punti per carta futura, molto meno delle
differenze di qualità tra le carte di una busta. Va però sempre nella direzione giusta, è tarato sui
dati e non costa nulla, quindi è **attivo di default**. La UI mostra i colori aperti.

Osservazione sui risultati (non sull'accordo): in Pick Two chi finisce nei colori più aperti vince di
più (53,0% → 55,2% → 57,8% per terzili, aperti − chiusi +4,8 ± 2,3); in Premier non si vede una
differenza (53,0 / 52,1 / 53,7 ± 1,2). Sono dati osservazionali, quindi indicativi.

### 2. Impegno nei primi pick: nessun vantaggio misurabile nel modello

Variante: λ (quanto le probabilità degli archetipi si concentrano) moltiplicato per un fattore < 1 nella
busta 1, scelto sui draft prima del taglio.
- La griglia è piatta: Pick Two 35,2–35,4%, Premier ×0,6 di poco sopra ×1.
- Fuori campione: −0,2 punti (Premier [−0,4, −0,1], Pick Two [−0,4, +0,1]).

Il motore si impegna già quanto i giocatori forti; impegnarsi di meno non lo avvicina alle loro scelte.
Resta disponibile (`fit-extras --early-temp`), ma è spento.

Dati sui risultati (osservazionali, al netto del livello del giocatore e della qualità dei primi pick),
Premier / Pick Two:
- chi ha preso nei primi pick meno di metà carte dei colori finali, cioè **ha cambiato tardi**, vince
  4,4 ± 2,2 / 3,0 ± 2,2 punti in meno;
- chi nei primi pick tocca **3 colori** fa un po' meglio sia di chi resta su 1-2 sia di chi si disperde
  su 4-5 (+0,8 contro −0,6/−0,7; +2,2 contro −1,0/−2,0). Gli intervalli sono larghi.

È coerente con l'idea "un colore più opzioni aperte", ma non abbastanza forte, né abbastanza separato
dalla qualità delle carte, per cambiare il modello senza una prova più diretta.

### Altro

- La vecchia variante dei segnali nelle probabilità dei colori resta spenta. Due verifiche danno
  ±0,007 di log-verosimiglianza con segno opposto: è rumore. La soglia per attivarla ora è 0,01.
- Test nuovi: offerta per colore, spostamento per direzione delle buste rimanenti, raccomandazione che
  cambia con il colore aperto, impegno ridotto nella busta 1.
