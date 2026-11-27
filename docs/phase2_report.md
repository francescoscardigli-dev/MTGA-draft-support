# Fase 2 — Parser di Player.log

Data: 2026-11-27. Stato: **codice e test pronti, NON verificato su log reali** (i log dell'utente non
sono ancora disponibili). Tutto quello che segue sulla struttura dei messaggi viene da fonti non
ufficiali (tool open source) e va confermato.

## Cosa c'è

Pacchetto `draftbot/arenalog/`:

| File | Contenuto |
|---|---|
| `parser.py` | parser incrementale (`LogParser.feed_line`), riusabile per la lettura live della Fase 7 |
| `validate.py` | controlli di coerenza basati solo sulla config del formato |
| `sanitize.py` | pulizia dei dati personali prima di salvare un log come fixture |
| `synth.py` | log sintetici: simula il tavolo con la config del formato, per i test |
| `paths.py` | percorsi tipici di `Player.log` (Windows, macOS; override `DRAFTBOT_PLAYER_LOG`) |

Comandi:
- `log-where`: dove il tool cerca il log;
- `log-drafts [file...] [-d] [--draft ID] [--json out.json]`: draft trovati, controlli, pick per pick con i nomi delle carte;
- `log-clean <in> <out> [--redact TESTO] [--keep-all]`: copia ripulita del log.

## Messaggi riconosciuti (da verificare)

Il riconoscimento si basa sulle **chiavi** degli oggetti JSON, non sul nome del metodo, e decodifica
le stringhe che contengono JSON (`request`, `Payload`). Così regge a piccole rinomine.

| Cosa | Chiavi cercate | Fonte attesa |
|---|---|---|
| Busta vista | `draftId`, `SelfPack`, `SelfPick`, `PackCards` | `Draft.Notify` |
| Pick fatto | `DraftId`, `GrpIds` o `GrpId`, `Pack`, `Pick` | `EventPlayerDraftMakePick` |
| Busta + pick | `DraftId`, `PackNumber`, `PickNumber`, `CardsInPack`, `PickGrpId(s)`, `EventId` | `LogBusinessEvents` |
| Evento | `EventName`, solo nel contesto di `EventJoin` | `EventJoin` |
| Pool finale | `InternalEventName`, `CardPool` | risposta di `EventGetCoursesV2` |

Gestiti: messaggi su una riga o con il JSON sulla riga successiva all'intestazione, righe
duplicate, più draft nello stesso log, `Player-prev.log` + `Player.log` letti in sequenza.

## Scelte

- **Numerazione di busta e pick non assunta.** Per ogni fonte si prova base 1 o 0 e pick contati
  per turno o per carta (in Pick Two potrebbe essere 1, 3, 5...). Si sceglie lo schema che torna
  con le dimensioni delle buste previste dal formato (per le buste) e con le carte prese contenute
  nella busta (per i pick). Se nessuno torna si usa base 1 e lo si scrive nelle note.
- **Formato:** dal prefisso del nome evento (`arena_event_prefixes` nella config del formato). Se
  manca, si deduce dalle dimensioni delle buste e dal numero di carte per pick, e lo si segnala.
- **Set:** dal token del nome evento che coincide con un codice di set configurato.
- **Controlli (`validate`)**: pick mancanti, dimensione della busta, numero di carte prese, carta
  presa presente nella busta, **wheel** (la busta che torna è contenuta in quella vista `pod_size`
  pick prima meno le carte prese), pool del corso uguale ai pick letti. Un dato mancante è una
  nota, un'incoerenza è un errore. Nulla viene inventato.
- **Messaggi non interpretati:** i metodi con "draft" nel nome che il parser non capisce vengono
  contati e mostrati da `log-drafts`, per adattare il parser ai log veri.

## Pulizia dei log (`log-clean`)

1. Per default tiene **solo** le righe di draft riconosciute e la loro intestazione.
2. Cancella i valori di chiavi sensibili (`screenName`, `userId`, `accountId`, `PlayerId`,
   `sessionId`, token, email...), a qualsiasi livello di escape; token JWT e Bearer; email; nome
   utente nei percorsi Windows e macOS; nomi `Nome#12345`; i testi passati con `--redact`.
3. Sostituisce UUID e ID account con pseudonimi coerenti: i collegamenti dello stesso draft restano.
4. Ricontrolla l'output e, se trova ancora qualcosa di sospetto, lo elenca ed esce con codice 2.

I test verificano che dal log pulito si ricostruisca lo stesso identico draft.

## Test

`tests/test_arenalog.py` (su Premier e Pick Two): draft completo, `LogBusinessEvents` con pick
contati per carta, base 0 con duplicati e JSON su riga separata, pick mancante segnalato, formato
dedotto senza nome evento, wheel incoerente rilevata, due draft nello stesso log, lettura parziale
(live), pulizia. Totale suite: 45 test.

Corretto anche un bug della Fase 1: `db.connect()` lasciava aperta una transazione e una seconda
connessione trovava il database bloccato.

## Limiti noti

- **Nessuna verifica su log reali.** I test sintetici provano la coerenza con la struttura attesa,
  non che i log veri abbiano quella struttura.
- Direzioni di passaggio di Pick Two: ancora da fonti secondarie (il controllo della wheel non ne dipende).
- Il deck inviato (`EventSetDeck*`) non è ancora letto: servirà nella revisione post-draft (Fase 8).
- I numeri di riga in `log-drafts` sono cumulativi se si leggono più file insieme.

## Checklist per quando arrivano i log

1. In Arena: Opzioni > Account > **Detailed Logs (Plugin Support)** attivo, poi un draft Premier e
   uno Pick Two. Copiare `Player.log` (e `Player-prev.log`) **prima** di riavviare Arena, che lo sovrascrive.
   - Windows: `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log`
   - macOS: `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
2. `python -m draftbot log-clean Player.log tests/fixtures/logs/premier_1.log --redact "<nome Arena>"`
3. Rileggere a mano il file pulito.
4. `python -m draftbot log-drafts tests/fixtures/logs/premier_1.log -d`: confrontare con il draft
   ricordato; guardare note, errori e "messaggi non interpretati".
5. Aggiungere test di regressione sui fixture reali e aggiornare questa pagina.
