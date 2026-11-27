# MTGA-draft-support

Assistente locale (Python 3.11+) per i draft di MTG Arena, nei formati Premier Draft e Pick Two
Draft. Legge `Player.log` in sola lettura, suggerisce i pick spiegando il punteggio e aiuta a
costruire il mazzo finale.

Stato: **Fase 1 completata** (database carte, mappatura ID Arena, `add-set`). Vedi `docs/plan.md` e i report in `docs/`.

## Uso

```bash
pip install -e .[dev]
python -m draftbot add-set TRK                 # crea la config (se manca) e importa le carte
python -m draftbot status TRK                  # config, formati e stato dei dati
python -m draftbot cards TRK --mv 2 --color W  # cerca carte per costo, colore, nome, rarita', tipo
python -m draftbot cards TRK --arena-id 106578 # carta corrispondente a un ID Arena
python -m draftbot issues TRK                  # carte senza ID Arena, conflitti, nomi non risolti
python -m pytest

# Sul PC con Arena installato, per usare anche il database del client:
python -m draftbot update-cards TRK --arena-db "C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw"
```

## Struttura

- `draftbot/`: motore, indipendente dal set
- `sets/<CODICE>.yaml`: config del set (codici Scryfall, espansione 17Lands, fonti degli ID Arena)
- `formats/<nome>.yaml`: struttura del formato (tavolo, carte per pick, buste, passaggio)
- `docs/`: piano e report

## Dati e condizioni d'uso

- Dati di gioco: [17Lands](https://www.17lands.com), dataset pubblici con licenza CC BY 4.0.
- Dati delle carte: [Scryfall](https://scryfall.com).
- Il tool legge solo il log di Arena: non automatizza click e non modifica il client. Wizards non
  ha preso una posizione esplicita sui tool di supporto al draft: usalo consapevolmente.
