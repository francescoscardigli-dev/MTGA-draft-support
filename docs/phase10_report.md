# Fase 10 — Miglioramenti dopo il piano (idee da altri tool)

Data: 2026-11-28. Le idee vengono dal confronto con altri progetti open source (draftomen,
nemedraft-client; vedi `phase2_report.md`, "Confronto con altri tool"). Ogni idea è verificata sui
dati 17Lands senza dati futuri (stima fino a una data di taglio, verifica dopo) e viene tenuta solo se
migliora.

## 1. Strato di imitazione dei giocatori forti (`draftbot/engine/imitation.py`)

**Idea** (da nemedraft, che dichiara il 69% di top-1 con una rete neurale non pubblica): imparare
direttamente come scelgono i giocatori forti, invece di affidarsi solo al valore atteso sul mazzo.

**Modello:** logit condizionale sulle opzioni della busta (in Pick Two: tutte le coppie). Le variabili
non dipendono dal set:
- `value`, il valore del motore (wheel e flusso dei colori compresi);
- `q`, la qualità delle carte;
- `pop`, la popolarità (1 − ATA normalizzata, dai draft fino al taglio);
- `fit`, la probabilità che le carte stiano nell'archetipo finale (somma delle P(archetipo));
- `rare`, `gold` e `land` (terre non base);
- le interazioni di valore, qualità, popolarità e fit con la fase del draft.

Stima per massima verosimiglianza sui pick dei giocatori della fascia alta prima del taglio.

**Verifica:** stesso set, draft dei giocatori forti dopo il taglio. Intervalli al 95% appaiati con
bootstrap sui draft. Output completo in `docs/backtest/imitazione_<formato>.txt`.

| | Motore | Con lo strato | Differenza | Top-3 |
|---|---|---|---|---|
| FRA Premier | 51,9% | **56,1%** | +4,3 [+3,3, +5,2] | 81,1% → 87,5% |
| FRA Pick Two | 37,4% | **40,5%** | +3,1 [+1,7, +4,3] | 58,7% → 65,0% |
| EOE Premier | 55,7% | **64,2%** | +8,5 [+7,5, +9,5] | 84,5% → 92,3% |
| OM1 Pick Two | 36,1% | **42,4%** | +6,3 [+5,0, +7,6] | 55,3% → 66,1% |

**Su set che non hanno contribuito alla stima:** i coefficienti stimati su FRA, usati senza ristima,
danno su EOE 64,3% (+8,6) e su OM1 43,1% (+7,1). Lo strato quindi generalizza, ed è il risultato che
conta per TRK.

**Varianti** (FRA Premier):
- solo valore, qualità e popolarità: peggio del motore (−2,9);
- con fit e carte: +3,8;
- con le fasi del draft: +4,3.

Il guadagno viene soprattutto da `fit` e `pop` insieme al valore. Il guadagno è più grande nell'inizio
e nella metà di ogni busta (Premier FRA: inizio 46,8% → 51,8%, metà 45,0% → 50,2%), cioè proprio dove
il motore si impegnava troppo presto sui colori.

**Cosa significa:** lo strato ordina le opzioni per **probabilità che un giocatore forte le scelga**.
Il valore del motore resta visibile ed è il primo ingrediente, ma pesa insieme alla popolarità della
carta e a quanto sta nei colori probabili. Imitare non è la stessa cosa che vincere: i giocatori
forti hanno abitudini, buone e cattive. È però la misura con cui il motore è stato verificato
fin dall'inizio, e qui il guadagno è grande e stabile su quattro set.

**Nel tool (attivo):**
- `priors/premier_imitation.yaml` (stimato su FRA + EOE, 46.950 pick) e `priors/pick_two_imitation.yaml`
  (FRA + OM1, 24.008 pick). Si rigenerano con `python -m draftbot fit-imitation FRA:2026-10-13
  EOE:2025-08-14 --format premier --prior-sets FRA HOB MSH`;
- la lettura live, la UI, `recommend` e la revisione ordinano per probabilità e mostrano
  "scelta dei forti NN%" accanto al valore;
- senza file salvato il motore torna all'ordine per valore.
