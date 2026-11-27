# MTGA-draft-support

Assistente locale (Python 3.11+) per i draft di MTG Arena, nei formati Premier Draft e Pick Two
Draft. Legge `Player.log` in sola lettura, suggerisce i pick spiegando il punteggio e aiuta a
costruire il mazzo finale.

Stato: **Fase 0 completata** (scheletro e verifiche). Vedi `docs/plan.md` e `docs/phase0_report.md`.

## Uso

```bash
pip install -e .[dev]
python -m draftbot --set TRK      # mostra config e formati del set
python -m draftbot sets           # elenca i set configurati
python -m pytest
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
