# MTGA-draft-support

Assistente locale (Python 3.11+) per i draft di MTG Arena, nei formati Premier Draft e Pick Two
Draft. Legge `Player.log` in sola lettura, suggerisce i pick spiegando il punteggio e aiuta a
costruire il mazzo finale.

Stato: **Fase 1 completata** (database carte, mappatura ID Arena, `add-set`); **Fase 2 scritta ma
non ancora verificata su log reali** (parser di `Player.log` e pulizia dei log); **Fase 3 completata**
(statistiche 17Lands calcolate in proprio, blend bayesiano, prior dai set precedenti); **Fase 4 completata**
(valutatore di mazzo, motore di pick a k carte e coppie in Pick Two, backtest); **Fasi 5 e 6 completate**
(sinergie misurate, costruzione del mazzo con 2-3 build); **Fase 7 completata** (wheel, segnali
provati e disattivati, lettura live di Player.log). Vedi `docs/plan.md` e i report in `docs/`.

## Uso

```bash
pip install -e .[dev]
python -m draftbot add-set TRK                 # crea la config (se manca) e importa le carte
python -m draftbot status TRK                  # config, formati e stato dei dati
python -m draftbot cards TRK --mv 2 --color W  # cerca carte per costo, colore, nome, rarita', tipo
python -m draftbot cards TRK --arena-id 106578 # carta corrispondente a un ID Arena
python -m draftbot issues TRK                  # carte senza ID Arena, conflitti, nomi non risolti
python -m pytest

# Log di Arena (attiva prima in Arena: Opzioni > Account > "Detailed Logs (Plugin Support)")
python -m draftbot log-where                   # dove il tool cerca Player.log
python -m draftbot log-drafts                  # draft trovati nel log, con controlli di coerenza
python -m draftbot log-drafts <file> -d        # pick per pick: busta vista e carte prese
python -m draftbot log-clean Player.log tests/fixtures/logs/premier_1.log --redact "<tuo nome Arena>"

# Statistiche 17Lands (dataset pubblici)
python -m draftbot update-data FRA             # scarica e aggrega game data e draft data
python -m draftbot stats FRA --format premier  # GIH WR bayesiana, OH, GNS, IWD, ATA, ALSA
python -m draftbot stats FRA --format premier --colors WU --tier high
python -m draftbot stats TRK --format pick_two # senza game data: stima dal solo prior
python -m draftbot fit-prior --format premier --sets FRA HOB MSH --half-life 7
python -m draftbot calibrate FRA --format premier --prior-sets HOB MSH

# Motore di pick
python -m draftbot fit-deck-model --format premier --sets FRA HOB MSH --lambda-set FRA
python -m draftbot recommend TRK --format premier --pack "Carta A;Carta B;Carta C" --pool "Carta D"
python -m draftbot recommend TRK --format pick_two --log      # ultimo pick letto da Player.log
python -m draftbot backtest FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH

# Sinergie e costruzione del mazzo
python -m draftbot tag-set FRA                 # tag dei temi (sets/FRA.tags.yaml, da rivedere a mano)
python -m draftbot synergy FRA --format premier
python -m draftbot build FRA --format premier --pool "carta;carta;..."
python -m draftbot build TRK --format pick_two --log  # pool dell'ultimo draft di Player.log
python -m draftbot build-check FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH

# Wheel, segnali e lettura live
python -m draftbot fit-extras FRA --format premier   # modello di wheel (e segnali, se aiutano)
python -m draftbot live                              # segue Player.log e suggerisce i pick
python -m draftbot check-extras FRA --format premier --cutoff 2026-10-13 --prior-sets HOB MSH

# Sul PC con Arena installato, per usare anche il database del client:
python -m draftbot update-cards TRK --arena-db "C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw"
```

## Struttura

- `draftbot/`: motore, indipendente dal set
- `sets/<CODICE>.yaml`: config del set (codici Scryfall, espansione 17Lands, fonti degli ID Arena)
- `draftbot/arenalog/`: parser di Player.log, pulizia dei dati personali, log sintetici per i test
- `draftbot/stats/`: aggregazione dei dati 17Lands, metriche, blend bayesiano, prior, calibrazione
- `draftbot/engine/`: valutatore di mazzo, proiezione del mazzo finale, archetipi, pick, backtest
- `draftbot/synergy/`: tag dei temi, misura delle sinergie
- `tags/themes.yaml`: regole generiche dei temi; `sets/<CODICE>.tags.yaml`: tag del set (rivisti a mano)
- `priors/<formato>.yaml`: coefficienti del prior (generati da `fit-prior`, versionati)
- `formats/<nome>.yaml`: struttura del formato (tavolo, carte per pick, buste, passaggio)
- `docs/`: piano e report

## Dati e condizioni d'uso

- Dati di gioco: [17Lands](https://www.17lands.com), dataset pubblici con licenza CC BY 4.0.
- Dati delle carte: [Scryfall](https://scryfall.com).
- Il tool legge solo il log di Arena: non automatizza click e non modifica il client. Wizards non
  ha preso una posizione esplicita sui tool di supporto al draft: usalo consapevolmente.
