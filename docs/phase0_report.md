# Fase 0 — Report delle verifiche preliminari

Data: 2026-11-27. La numerazione delle fasi segue il piano rivisto in `docs/plan.md`. Ogni esito dice se l'ho verificato direttamente sui dati
(**[verificato]**) o se viene da fonti secondarie (**[fonte secondaria]**).

## Sintesi

| # | Verifica | Esito |
|---|---|---|
| 1 | Dati 17Lands TRK/SDS | **Parziale.** Draft data presenti per Premier e Pick Two. **Game data assenti** per TRK. |
| 2 | Licenza 17Lands | Dataset pubblici CC BY 4.0. I dati delle pagine del sito (card data, color data) sono **riservati a 17Lands.com** secondo la nota inclusa nelle risposte. |
| 3 | Termini di Arena | **Nessuna dichiarazione esplicita** di Wizards sui tool di supporto al draft. Rischio residuo. |
| 4 | Tool esistenti | Quattro tool coprono già i rating per carta; mancano segnali calibrati e logica Pick Two. |
| 5 | Formato di Player.log | Struttura nota da fonti secondarie. **Nessun log di esempio disponibile** (bloccante per la Fase 2, parser). |
| 6 | Mappatura ID Arena → Scryfall | Scryfall non ha `arena_id` per TRK/SDS. 17Lands fornisce `mtga_id` per tutte le 336 carte. |

## 1. Dati 17Lands per TRK e SDS

**Dataset pubblici** [verificato su `www.17lands.com/api/public_datasets`, aggiornati al 2026-11-26]:

| Formato | draft | game | replay |
|---|---|---|---|
| TRK PremierDraft | sì | **no** | no |
| TRK PickTwoDraft | sì | **no** | no |
| TRK TradDraft | sì | no | no |

**Volumi dei draft data** [verificato scaricando i file]:

| | Premier | Pick Two |
|---|---|---|
| Draft | 23.785 | 12.359 |
| Righe (un pick per riga) | 998.949 | 259.534 |
| Periodo | 2026-11-10 → 2026-11-22 | 2026-11-10 → 2026-11-22 |

Struttura delle colonne:
- metadati: `draft_id`, `rank`, `event_match_wins/losses`, `pack_number`, `pick_number`, `pick`, `pick_2`, `user_game_win_rate_bucket`, `user_n_games_bucket`, …;
- 341 colonne `pack_card_*` e 341 `pool_*`.

In Pick Two la seconda carta è nella colonna `pick_2`.

**Le carte SDS sono dentro il set TRK** in tutte le fonti 17Lands [verificato]:
- le 30 carte hanno colonne proprie nei draft data;
- compaiono nei pack (2.957 avvistamenti nei primi 400k pick di Pick Two, 3.630 in Premier);
- hanno un `mtga_id` (da 106931 a 106960) nei card ratings.

In pratica `seventeenlands_expansion: TRK` copre già entrambi i codici.

**Struttura dei formati ricavata dai dati** [verificato]:

| Formato | Dimensione busta per pick | Wheel |
|---|---|---|
| Premier | 14 → 13 → … → 1 | La busta del pick 1 torna al pick 9 (100% dei casi) |
| Pick Two | 14 → 12 → … → 2 (7 pick per busta) | La busta torna al pick 5 con 6 carte (98,5% dei casi) |

In Pick Two la wheel al pick 5 implica un **tavolo da 4 giocatori**. La wheel esiste quindi anche in Pick Two, ma in un punto diverso. Questi valori sono in `formats/*.yaml`, coperti da test.

**Statistiche aggregate del sito** (endpoint usati dalle pagine, non un'API documentata; vedi sezione 2 per i limiti d'uso):

- **Color pair**:
  - Premier: 277.639 partite totali (la coppia meno giocata, WU, ne ha 11.379);
  - Pick Two: 61.071 partite totali (la coppia meno giocata, WU, ne ha 2.244).
- **Statistiche per carta** (aggiornato su tua segnalazione):
  - L'endpoint che avevo interrogato all'inizio (`/card_ratings/data`) restituisce dati ridotti: GIH WR per 79 carte su 336 in Premier, per nessuna in Pick Two.
  - La pagina Card Data del sito usa invece `/api/card_data`, che è **completo**:

    | Formato | Carte con GIH WR e IWD | Partite GIH per carta (mediana / max) |
    |---|---|---|
    | Premier | 300 su 336 | 5.475 / 42.622 |
    | Pick Two | 260 su 336 | 1.262 / 8.760 |

  - **Però la risposta stessa dell'endpoint dice:** *"This data is only for use on 17Lands.com. The only data permitted for outside use (public or private) is available at 17lands.com/public_datasets."* Quindi questi dati **non sono utilizzabili dal tool, nemmeno per uso privato**: le condizioni le pone 17Lands, che è il titolare. Non li salvo e non li importo.

**Conseguenze sul piano:**
- La Fase 3 (statistiche: GIH WR, IWD) e la Fase 5 (lift delle sinergie) dipendono dai **game data di TRK, non ancora pubblicati**. Finché mancano, si usano i dati dei draft (ALSA, ATA, ordine di pick dei giocatori forti) più i prior dei set precedenti (vedi la partenza a freddo nella sezione 3 del piano).
- ALSA e ATA si possono calcolare **in proprio** dai draft data, già ora e per entrambi i formati.
- Set precedenti con game data Pick Two (utili come prior e per il test di generalità): FRA (2026-10-17, anche Premier), HOB e MSH (anche replay), OM1.
- **Pick Two è utilizzabile**: circa la metà dei draft di Premier, ma con un quarto delle partite. I segnali Pick Two saranno meno affidabili, come previsto.

**Nota sul dominio:** l'API ufficiale di 17Lands indica i file su `data.17landscapes.com` (tutti i 319 link). È un dominio diverso da `17lands.com`, ma è quello pubblicato dall'API e usato anche per le immagini delle carte (`cards.17landscapes.com`). L'ho aggiunto alla lista consentita dell'ambiente cloud.

## 2. Licenza e condizioni d'uso 17Lands

- I dataset pubblici sono rilasciati di norma con **CC BY 4.0**: si possono usare citando 17Lands [fonte secondaria: testo delle linee guida riportato dai risultati di ricerca; la pagina del sito è generata via JavaScript e non sono riuscito a leggerla direttamente].
- Per i **dati curati** (pagine Card Data e Color Data), le [usage guidelines](https://www.17lands.com/usage_guidelines) chiedono ai tool di terzi di non mostrarli prima del **giorno 12 dopo l'uscita** del set su Arena.
- Più restrittiva ancora è la nota inclusa nelle risposte di `/api/card_data` [verificato]: *"This data is only for use on 17Lands.com. The only data permitted for outside use (public or private) is available at 17lands.com/public_datasets."* Le due indicazioni non sono del tutto coerenti tra loro; prevale la più restrittiva finché 17Lands non chiarisce.

**Decisione proposta:**
- Basare il tool **sui dataset pubblici** (CC BY 4.0, con attribuzione nel README e nella UI).
- **Non usare gli endpoint del sito** (`/api/card_data`, `/card_ratings/data`, `/color_ratings/data`): la loro stessa risposta lo vieta fuori da 17Lands.com. GIH WR, IWD e win rate per color pair si calcolano dai game data pubblici, appena 17Lands li pubblica per TRK.
- Download non aggressivi, con cache locale.

## 3. Termini d'uso di Arena

- Non ho trovato una dichiarazione ufficiale di Wizards che autorizzi o vieti i tool di supporto al draft.
- I [termini di Arena](https://magic.wizards.com/en/mtgarena/terms) contengono clausole generiche. Il codice di condotta vieta hacking e modding del client.
- I progetti open source dichiarano che la questione non è stata chiarita ([MTG Arena Tool](https://github.com/CodySchrank/MTG-Arena-Tool)).
- I vendor ([Arena Tutor](https://draftsim.com/arenatutor/)) affermano che Wizards permette le app che non violano i ToS.
- Esistono da anni diversi overlay di draft molto diffusi (17Lands, Untapped, Arena Tutor) [fonte secondaria].

**Valutazione:** il nostro progetto rientra nella categoria più prudente: sola lettura del log, nessuna iniezione, nessun click automatico, nessuna modifica del client. Il rischio residuo non è eliminabile. Lo documento nel README e non procedo oltre su questo punto senza tua indicazione.

## 4. Tool esistenti

| Tool | Cosa fa (da fonti pubbliche, non verificato a mano) |
|---|---|
| [MTGA_Draft_17Lands](https://github.com/unrealities/MTGA_Draft_17Lands) (open source) | Overlay con le statistiche 17Lands per carta. In Pick Two pesa il pick N come il pick 2N−1 e **nasconde le percentuali di wheel**, perché il suo modello assume tavoli da 8 con una carta per pick. |
| [Untapped.gg Draftsmith](https://mtga.untapped.gg/companion) | Rating che si adattano al pool e suggerimento del mazzo a fine draft. |
| [Arena Tutor (Draftsim)](https://draftsim.com/arenatutor/) | Suggerimento dei pick che tiene conto del pool e deck builder. |
| [Draft Omen](https://www.draftomen.com/docs/) | App read-only con win rate 17Lands; supporta anche Pick Two. |

**Cosa manca, e su cui concentrarsi:**
- segnali **calibrati** sui draft pubblici, con confidenza;
- wheel tracker corretto per Pick Two (wheel al pick 5, tavolo da 4);
- suggerimento a **coppie** in Pick Two;
- punteggio scomposto e spiegato;
- sinergie misurate (lift condizionato all'archetipo);
- confronto tra 2-3 build alternative con punteggio atteso.

## 5. Formato di Player.log

Da fonti secondarie ([manasight](https://blog.manasight.gg/arena-log-format-guide/), [draftomen PR #799](https://github.com/andreagrandi/draftomen/pull/799), [mtga-draft-tracker](https://github.com/minimapletinytools/mtga-draft-tracker)):

- **Percorsi:**
  - Windows: `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log`
  - macOS: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- **Pack mostrato**: `Draft.Notify {"draftId", "SelfPack", "SelfPick", "PackCards": "id,id,…"}`, con numerazione 1-based.
- **Pick effettuato**: `EventPlayerDraftMakePick`, con `GrpIds` che in Pick Two contiene **due** ID.
- **Casi limite riportati:**
  - righe duplicate;
  - un pick a volte non registrato in Pick Two;
  - dubbio se il primo pack del draft generi `Draft.Notify`.
- Il formato non è un'API pubblica e cambia con gli aggiornamenti del client.

**Esito: bloccante per la Fase 2.** Non ho log di esempio, né di Premier né di Pick Two. Le fixture open source trovate riguardano Quick Draft, che non è supportato, e hanno licenza da verificare. Servono i tuoi log, ripuliti dai dati personali prima del commit (in Fase 1 o 2 scrivo uno script che lo fa). Nel frattempo il parser si può scrivere sulla struttura documentata, ma **non lo considererò verificato** finché non passa su log reali.

## 6. Mappatura ID Arena → Scryfall

[verificato su Scryfall e 17Lands, 2026-11-27]

**Scryfall:**
- TRK ha 586 stampe, nessuna con `arena_id`; SDS ha 30 carte, nessuna con `arena_id`.
- Il flag `booster` è falso ovunque: non è ancora aggiornato.

**17Lands:**
- I card ratings riportano `mtga_id` per **tutte le 336 carte giocabili nel draft** (306 TRK + 30 SDS).
- **335 su 336** corrispondono per nome a Scryfall. Unica eccezione: `All Good Things . . .`, che va normalizzata in Fase 1.

**Database del client Arena** (`Raw_CardDatabase_*.mtga`):
- è la fonte consigliata dalla comunità per i set nuovi, ma esiste solo sul tuo PC;
- lo leggerà `add-set` in locale, ma da qui non posso verificarlo.

Per le carte TRK vale `mtga_id = 106577 + numero di collezione` (305 casi su 305). È una regolarità osservata, **non la userò nel motore**: il vincolo è segnalare le carte senza mappatura, non indovinarle.

**Strategia per la Fase 1:**
- La priorità delle fonti è definita in config: database del client, poi 17Lands, poi Scryfall.
- Le carte vengono abbinate per set e numero di collezione oppure per nome normalizzato.
- Ogni ID senza corrispondenza viene riportato in un elenco esplicito.

## Decisioni e azioni

1. **Set per il test di generalità:** propongo **FRA**. Ha draft e game data sia Premier sia Pick Two, quindi copre entrambi i formati.
2. **Dati di gioco TRK assenti:** la Fase 3 (statistiche) parte con i dati disponibili (draft data e rating parziali) e la struttura per i game data. Il calcolo di GIH WR e IWD e i test di calibrazione si fanno su FRA finché TRK non viene pubblicato.
3. **Log reali:** necessari prima di chiudere la Fase 2.
4. **Ambiente cloud:** ho creato l'ambiente `mtga-draft-support`, che consente solo `api.scryfall.com`, `data.scryfall.io`, `www.17lands.com` e `data.17landscapes.com`.
