# Piano rivisto (approvato il 2026-11-27)

Modifiche rispetto al piano originale, approvate:

- **Config di formato separata** (`formats/*.yaml`): giocatori al tavolo, carte per pick, buste,
  dimensione busta, direzioni di passaggio. Wheel e segnali vengono calcolati da questi parametri.
- **Suggerimento a k carte**: `recommend(pool, pack, k)`. In Pick Two si valutano le coppie.
- **Mappatura ID Arena**: fonti in ordine di priorità definito in config (database locale del
  client, poi 17Lands, poi Scryfall). Gli ID senza corrispondenza vengono segnalati.
- **Parser del log anticipato** alla Fase 2, testato su log reali ripuliti.
- **Valutatore di mazzo** come componente centrale, riusato da pick, deckbuilding e backtest.
- **Snapshot dei dati con data** e file dei tag rivisti a mano versionati nel repo
  (mai sovrascritti da `add-set`).
- **Punteggio**: stima probabilistica sugli archetipi al posto dei pesi fissati a mano, forza per
  archetipo, sinergie con regressione regolarizzata condizionata alla color pair, valore di
  rimpiazzo, decadimento temporale dei dati.
- **Backtest**: accordo top-1 e top-3 con i giocatori forti, più il confronto tra il punteggio
  atteso della build proposta e quello del mazzo reale.
- **Comandi aggiuntivi**: `status`/`doctor`, `update-data`, storico personale dei draft.
- **UI**: web locale (FastAPI con WebSocket).
- **Modulo regole**: rinviato.

## Fasi

| Fase | Contenuto |
|---|---|
| 0 | Verifiche, scheletro del repo, config di set e formato (vedi `phase0_report.md`) |
| 1 | Database carte (Scryfall), costi e pip, mappatura degli ID Arena, `add-set` |
| 2 | Parser di Player.log su log registrati (Premier, Pick Two) |
| 3 | Statistiche 17Lands, blend bayesiano, prior dai set precedenti, snapshot con data |
| 4 | Valutatore di mazzo e motore di pick (k carte per pick), backtest |
| 5 | Sinergie (regressione regolarizzata e tag LLM rivisti a mano) |
| 6 | Deckbuilding (2-3 build, terre, splash) |
| 7 | Segnali e wheel parametrici, lettura live del log |
| 8 | UI web locale, revisione post-draft |
| 9 | Rifinitura, test di generalità (set proposto: FRA), documentazione di `add-set` |
