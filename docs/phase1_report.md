# Fase 1 — Database carte e comando add-set

Data: 2026-11-27.

## Cosa fa

`python -m draftbot add-set <CODICE>`:
1. Se `sets/<CODICE>.yaml` non esiste, lo crea dai metadati di Scryfall e 17Lands: set principale più gli eventuali fogli bonus di tipo `masterpiece`, formati con draft data disponibili. **Una config esistente non viene mai sovrascritta.**
2. Scarica da Scryfall le carte dei set configurati.
3. Ricava l'elenco delle carte che compaiono nelle buste dall'intestazione dei draft data pubblici di 17Lands (scarica solo la prima riga). Così sono incluse le carte di altri set inserite nelle buste, come le Special Guests in FRA.
4. Risolve per nome su Scryfall le carte che non appartengono ai set configurati.
5. Mappa gli ID Arena (grpId), con le fonti nell'ordine della config:
   - database del client Arena, se presente sul PC;
   - `cards.csv` pubblico di 17Lands;
   - Scryfall.

   Le carte dei set del draft si mappano solo con ID di quei set. Le carte ospiti accettano ID di qualsiasi set.
6. Salva tutto in SQLite (`data/draftbot.sqlite`), registra ogni download con data e fonte (tabella `snapshots`) ed elenca i problemi in una tabella dedicata.

Altri comandi:
- `update-cards <CODICE>`: riscarica le carte;
- `cards <CODICE> [--name --mv --color --rarity --type --arena-id -v]`: interroga il database;
- `issues <CODICE>`: elenca i problemi dell'ultimo import;
- `status <CODICE>`: mostra config e stato dei dati.

## Risultati

| Set | Carte | ID Arena | Problemi |
|---|---|---|---|
| TRK | 341 (311 TRK, di cui 5 terre base, e 30 SDS) | 450 | nessuno |
| FRA | 295 (285 FRA e 10 Special Guests) | 431 | nessuno |

`add-set FRA` è partito da zero: ha creato `sets/FRA.yaml` e importato le carte senza modifiche al motore.

## Scelte e limiti

- **Costi di mana:**
  - Pip per colore, ibridi, phyrexiani, X e `{2/W}`.
  - Per le carte con più facce lanciabili (split, "prepare", adventure) i pip sono il massimo per colore tra le facce.
  - Per le carte bifronte conta solo la faccia frontale.
  - Il mana value è quello di Scryfall.
- **Nomi:** confronto normalizzato (maiuscole, apostrofi tipografici, `. . .` → `...`, faccia frontale). Le eccezioni non risolte si aggiungono a mano in `name_aliases` nella config del set.
- **Database del client Arena:** lo schema non è documentato. Il lettore segue quello descritto dai tool open source e, se non lo riconosce, lo segnala invece di indovinare. **Non è verificato su un'installazione reale**: va provato sul tuo PC con `python -m draftbot update-cards TRK --arena-db "<cartella Raw>"`.
- **Carte ospiti:** la stampa di riferimento è quella restituita da Scryfall (es. MM2), non quella SPG. La rarità mostrata è quindi quella della stampa originale. Non incide sul draft, ma andrà considerata per i prior per rarità (Fase 3).
- **Rarità di SDS:** per Scryfall sono tutte mythic, mentre 17Lands ne classifica alcune come rare. Si usa Scryfall.
- **Tag LLM di meccaniche e archetipi:** spostati nella fase delle sinergie (Fase 5 del piano rivisto), dove servono. `add-set` lascia `mechanics` e `archetypes` vuoti.
- **Indice delle regole:** rinviato, come deciso.
- **Fonti 17Lands:** si usano solo i file pubblici (indice dei dataset, intestazioni dei draft data, `cards.csv`). Nessun endpoint del sito.
