# Guida d'uso

Assistente locale per i draft di MTG Arena (Premier Draft e Pick Two Draft). Legge `Player.log` in
sola lettura, suggerisce i pick spiegando il punteggio e propone il mazzo finale. Non clicca nulla e
non modifica il client.

## 1. Installazione (una volta)

Serve Python 3.11 o successivo.

```bash
git clone <repository> && cd MTGA-draft-support
pip install -e .[ui]
```

In Arena: **Opzioni > Account > "Detailed Logs (Plugin Support)"** attivo. Senza questa opzione i
messaggi del draft non vengono scritti nel log.

## 2. Preparare i dati di un set (una volta per set, poi ogni tanto)

```bash
python -m draftbot add-set TRK          # carte (Scryfall), ID Arena, config del set
python -m draftbot update-data TRK      # dataset pubblici 17Lands (game e draft data)
python -m draftbot tag-set TRK          # tag dei temi di sinergia (da rivedere a mano)
python -m draftbot status TRK           # cosa c'e' e cosa manca
```

`update-data` va rilanciato quando 17Lands aggiorna i dati (circa ogni settimana); riscarica solo i
file cambiati.

Sul PC con Arena installato si può usare anche il database delle carte del client, che è più
aggiornato per i set appena usciti:

```bash
python -m draftbot update-cards TRK --arena-db "C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw"
```

I modelli generici (`priors/*.yaml`: prior delle carte, valutatore di mazzo, wheel) sono già nel
repository e valgono per tutti i set.

## 3. Durante il draft

```bash
python -m draftbot ui                   # poi apri http://127.0.0.1:8765
```

oppure, senza browser:

```bash
python -m draftbot live
```

Il percorso standard del log è:
- Windows: `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log`
- macOS: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`

Se è altrove, usa `--log <percorso>`. Se il set o il formato non vengono riconosciuti dal nome
dell'evento, usa `--set TRK --format premier`.

### Come leggere i numeri

- **Qualità** di una carta: di quanti punti la sua win rate (quando è in mano, GIH WR) supera la
  media del set. È una stima bayesiana: con pochi dati resta vicina al prior, calcolato da come i
  giocatori scelgono la carta (ATA, ALSA) sui set precedenti.
- **Valore di un'opzione**: di quanti punti migliora la win rate attesa del mazzo finale, mediato
  sugli archetipi con la loro probabilità. Una carta che non entrerebbe nelle 23 finali vale circa 0.
- **Ritorno atteso**: valore atteso della carta migliore che tornerà dopo un giro del tavolo.
- **Archetipi probabili**: probabilità di finire in ogni coppia di colori, dato il pool.
- **Colori aperti**: per ogni colore, quanta offerta di carte giocabili arriva in più (verde) o in
  meno (rosso) del previsto, per pick. Il motore ne tiene conto: un colore aperto alza il valore
  atteso delle carte future in quel colore, in proporzione a quanto i dati 17Lands mostrano che il
  flusso continua (stessi vicini nella busta 3).
- In Pick Two le opzioni sono coppie.

### Limiti da tenere a mente

- Nei primi pick il motore si impegna sui colori un po' troppo presto (Fase 4): se una bomba fuori
  colore ha un valore poco sotto la prima opzione, vale la pena considerarla.
- Le sinergie si vedono come temi, ma non sono nel punteggio (Fase 5).
- Per un set senza game data (TRK al 2026-11-27) la qualità viene dal solo prior: l'ordine tra le
  carte è affidabile, i valori assoluti meno.
- Lo splash è proposto raramente (Fase 6).

## 4. Dopo il draft

```bash
python -m draftbot build TRK --format premier --log      # 2-3 mazzi dal pool dell'ultimo draft
python -m draftbot review                                # pick per pick contro il motore
```

Entrambi sono anche nella UI: il riquadro "Mazzi proposti" e la scheda "Revisione".

## 5. Comandi utili

| Comando | A cosa serve |
|---|---|
| `stats TRK --format premier` | qualità delle carte, GIH WR, IWD, ATA, ALSA |
| `stats TRK --format premier --colors WU` | per archetipo |
| `recommend TRK --format premier --pack "A;B;C" --pool "D;E"` | consiglio da elenchi di carte |
| `cards TRK --name Picard -v` | cercare una carta |
| `log-drafts` | draft trovati nel log, con controlli di coerenza |
| `log-clean <in> <out> --redact "<nome>"` | copia del log senza dati personali |

## 6. Aggiungere un nuovo set

1. `python -m draftbot add-set <CODICE>`. Se `sets/<CODICE>.yaml` non esiste, lo crea:
   - set principale ed eventuali fogli bonus da Scryfall;
   - formati con draft data pubblici su 17Lands;
   - fonti degli ID Arena.

   Poi scarica le carte e mappa gli ID. Una config esistente non viene mai sovrascritta.
2. Controlla `python -m draftbot issues <CODICE>`. Le differenze di nome tra le fonti si risolvono
   in `name_aliases` nella config.
3. `python -m draftbot update-data <CODICE>`.
4. `python -m draftbot tag-set <CODICE>`, poi rivedi a mano `sets/<CODICE>.tags.yaml`.
5. Se ci sono game data: `python -m draftbot synergy <CODICE> --format <f>`.
6. `python -m draftbot status <CODICE>` per controllare.

Nessun passo richiede di modificare il codice: tutto ciò che dipende dal set sta in
`sets/<CODICE>.yaml` e `sets/<CODICE>.tags.yaml`, e un test lo verifica. Il test di generalità
della Fase 9 lo ha fatto su due set mai visti prima (vedi `phase9_report.md`).

## 7. Aggiornare i modelli generici (facoltativo)

Quando escono set nuovi con game data si possono ristimare i modelli generici:

```bash
python -m draftbot fit-prior --format premier --sets FRA HOB MSH --half-life 7
python -m draftbot fit-deck-model --format premier --sets FRA HOB MSH --lambda-set FRA
python -m draftbot fit-extras FRA --format premier
```

E verificarli senza dati futuri:

```bash
python -m draftbot calibrate FRA --format premier --prior-sets HOB MSH
python -m draftbot backtest FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH
python -m draftbot build-check FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH
python -m draftbot check-extras FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH
```

## Dati e condizioni d'uso

- Dati di gioco: [17Lands](https://www.17lands.com), dataset pubblici, licenza CC BY 4.0. Il tool non
  usa le API riservate al sito.
- Dati delle carte: [Scryfall](https://scryfall.com).
- Wizards non ha preso una posizione esplicita sui tool di supporto al draft. Questo tool legge solo
  il log e non automatizza nulla: usalo consapevolmente.
