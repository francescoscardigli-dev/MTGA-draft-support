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

## 2. Fixing: terre doppie nel valutatore, nel costruttore e nel pick

**Problema:** prima il motore trattava una terra non base come una magia, che competeva per i 23
posti delle magie con la sua qualità (GIH WR, tra −1 e −4 punti per le doppie di FRA). Così una
doppia non entrava mai nel mazzo proiettato e al pick valeva circa 0, mentre i giocatori forti la
prendono in media intorno al pick 6-9 (ATA).

**Misura sui mazzi 17Lands** (8 set/formati: FRA, HOB, MSH, EOE, OM1). Le terre di fixing sono le
terre non base che producono almeno 2 colori del mazzo, comprese quelle di ogni colore e quelle che
cercano una base, contate fino a 4. Effetto in punti di win rate per terra, al netto di qualità,
curva, colori e livello del giocatore:
- mazzi a 3 colori: da +0,45 a +1,07 in tutti gli 8 casi;
- mazzi a 2 colori: da +0,1 a +0,8;
- 2 colori con splash: da −0,1 a +0,5.

I fixer che non sono terre (Treasure, landcycling) non mostrano un effetto netto e contano già come
magie.

**Modifiche:**
- **valutatore di mazzo:** qualità media e top 3 sulle sole magie. In più tre variabili: terre di
  fixing nei mazzi a 2 colori, con splash e a 3 colori. Modelli salvati, stimati su FRA, HOB e MSH:
  Premier +0,14 / +0,15 / +0,40, Pick Two +0,36 / +0,08 / +0,49;
- **catalogo:** le terre "di ogni colore" e quelle che cercano una base producono tutti i colori;
- **proiezione al pick:** per ogni archetipo, le terre di fixing valgono secondo la quota di mazzi a
  2 colori, con splash e a 3 colori del formato. Con 3 colori, il terzo colore è considerato
  ugualmente probabile tra i 3 rimasti;
- **costruttore:** nessuna regola nuova; il valutatore premia ora il fixing.

**Priorità al pick che ne risulta** (valore di una terra, prima dello strato di imitazione):

| | Doppia nei due colori dell'archetipo | Doppia con un solo colore dell'archetipo |
|---|---|---|
| Premier | +0,18 punti | +0,03 |
| Pick Two | +0,30 punti | +0,04 |

Per confronto, una magia di 2 punti di qualità sopra la 23ª carta del mazzo vale circa +0,14 /
+0,18 (solo dalla media). Quindi una doppia nei propri colori vale più o meno quanto una buona
magia giocabile. Il valore è pesato con la probabilità degli archetipi: nei primi pick, quando i
colori sono incerti, una doppia vale poco per il motore.

**Verifica** (stima fino al 13/10, verifica dopo, motore completo con wheel e flusso):

| Top-1 | Motore, senza → con fixing | Con lo strato, senza → con fixing |
|---|---|---|
| FRA Premier | 51,9% → 52,7% | 56,1% → 56,7% |
| FRA Pick Two | 37,4% → 37,6% | 40,5% → 40,6% |
| EOE Premier | 55,7% → 56,2% | 64,2% → 64,3% |
| OM1 Pick Two | 36,1% → 37,5% | 42,4% → 42,9% |

- La log-verosimiglianza dello strato migliora in tutti e quattro i casi.
- **Costruttore** (`build-check` FRA): il test sui risultati ora va nella direzione giusta. Quando i
  nostri colori sono diversi da quelli giocati, il mazzo reale va peggio: Premier −2,15 contro −4,84,
  prima −2,95 contro −1,41; Pick Two −1,74 contro −4,09, prima −2,32 contro −2,86. Stessi colori
  principali: Premier 80%, Pick Two 64% → 66%.
- Valutatore fuori campione: correlazione Premier 0,114 → 0,116, Pick Two 0,127 → 0,130.

**Quanto spesso si prende una terra non base quando ce n'è una nella busta** (FRA, draft di verifica):

| | Giocatori forti | Motore | Con lo strato |
|---|---|---|---|
| Premier | 17% | 11% | 15% |
| Pick Two | 30% | 15% | 27% |

Il motore da solo prende poche terre nei primi pick (3% contro 8% in Premier, 3% contro 17% in Pick
Two). Lo strato di imitazione corregge quasi del tutto: Premier 4% contro 8%, ma 21% contro 24% a metà
busta. Il conteggio comprende anche le terre non base che non fanno fixing.
