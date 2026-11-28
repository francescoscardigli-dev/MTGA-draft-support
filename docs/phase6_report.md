# Fase 6 — Costruzione del mazzo (2-3 build, terre, splash)

Data: 2026-11-28. Sviluppo e verifica su **FRA**, con modelli stimati fino al 2026-10-13 e
verifica sui draft successivi dei giocatori della fascia alta.

## Come funziona

`python -m draftbot build <SET> --format <f> --pool "carta;carta;..."` oppure `--log [Player.log]`
(usa il pool dell'ultimo draft letto dal log). Codice: `draftbot/engine/build.py`.

Combinazioni provate: le 10 coppie di colori, ogni coppia con splash di un terzo colore, i 10 mazzi a
tre colori principali. Per ognuna:

1. **Magie**: si parte dalle 23 migliori per qualità tra quelle giocabili e si migliora con scambi
   uno-a-uno finché cresce il punteggio del valutatore di mazzo della Fase 4 (qualità media, top 3,
   carte di costo ≤ 2 e ≥ 5, terre, splash, colori in più).
2. **Splash**: solo carte con un solo simbolo del colore di splash, al massimo 3, e solo se almeno una
   ha qualità ≥ +2 punti.
3. **Tre colori principali**: almeno 4 carte per colore. Con meno è uno splash, già coperto al punto 2.
4. **Terre**:
   - 17, oppure 16 con curva bassa (costo medio ≤ 2,75 e al massimo 3 carte di costo ≥ 5);
   - prima le terre non base nei colori del mazzo;
   - poi le base: almeno 3 fonti per ogni colore principale e 3 per lo splash, il resto in
     proporzione ai simboli colorati.
5. Si mostrano le **3 build migliori** distinte, con la win rate attesa per un giocatore medio e la
   distanza dalla prima.

Le regole su terre, splash e tre colori sono **euristiche dichiarate**, non stimate. Il valutatore dà
circa −0,9 punti per ogni terra oltre le 17, ma l'effetto è confuso con la curva (i mazzi aggressivi
giocano 16 terre), quindi non lo uso per scegliere il numero di terre al di fuori di 16-17.

## Verifica su FRA (300 draft dopo il taglio, giocatori della fascia alta)

Il pool finale (draft data) è unito al mazzo giocato (game data, prima build) con il `draft_id`.
Output completi in `docs/backtest/FRA_premier_build.txt` e `FRA_pick_two_build.txt`.

| | Premier | Pick Two |
|---|---|---|
| Draft verificati | 297 | 300 |
| Stessi colori principali del giocatore | **80%** | **64%** |
| Magie della nostra build presenti nel mazzo reale | **86%** | **82%** |
| Stesso numero di terre | 74% | 78% |
| Splash: nostro / reale | **3% / 47%** | **2% / 44%** |
| Tre colori principali: nostro / reale | 25% / 26% | 33% / 38% |
| Punteggio nostra build − reale (a favore nostro per costruzione) | +1,0 punti | +1,5 punti |
| Win rate reale − prevista per la nostra build, stessi colori | −3,0 ± 1,4 | −2,3 ± 1,9 |
| Idem, colori diversi dai nostri | −1,4 ± 2,9 | −2,9 ± 2,6 |

**Come leggerli:**
- **Accordo**: nella maggior parte dei casi il costruttore sceglie gli stessi colori principali del
  giocatore forte e quasi le stesse magie.
- **Splash**: è la differenza più grande. I giocatori fanno splash molto più spesso del costruttore.
  Il valutatore, stimato sui dati, dice che a parità di qualità delle carte i mazzi con splash rendono
  circa 3 punti in meno. Può essere un effetto reale (mana peggiore) o in parte di selezione (si fa
  splash quando il pool è debole). Con questi dati non lo distinguo, quindi il costruttore resta
  prudente sullo splash, e lo segnalo come punto aperto.
- **Test sui risultati**: si confronta la win rate reale (al netto del livello del giocatore) con
  quella prevista per la nostra build. Se il costruttore sceglie bene, chi ha scelto colori diversi
  dai nostri dovrebbe rendere di meno. **Il test non lo mostra**: in Premier chi ha scelto
  diversamente ha reso un po' meglio, in Pick Two un po' peggio, ed entrambe le differenze sono ben
  dentro l'errore. Con 300 draft (circa 4 partite ciascuno) il test non ha la potenza per
  distinguere. Non c'è quindi evidenza che il costruttore scelga i colori meglio dei giocatori forti,
  ma neanche che scelga peggio.
- **Previsione troppo ottimista**: in tutti i gruppi la win rate reale è 1,4–3 punti sotto quella
  prevista per la nostra build. In parte è normale (la nostra build massimizza il valutatore, quindi
  ne eredita gli errori di stima a suo favore). In parte riflette la sovrastima di circa il 7% delle
  differenze già vista in Fase 4. La win rate attesa assoluta va presa come indicativa.
- **Differenza di punteggio**: la nostra build ha un punteggio più alto del mazzo reale per
  costruzione, perché massimizza proprio il valutatore. È solo una verifica di coerenza.

## Esempio di output (pool reale FRA Premier)

```
1. RG: win rate attesa 57.3% (giocatore medio)
   terre: 17 = 9 Mountain, 7 Forest, Konstrari Annex
   curva (costo: carte): 1: 1, 2: 9, 3: 5, 4: 3, 5: 3, 6+: 2; creature 11
   costo 1: Marwyn, the Clearcutter (-1.6)
   costo 2: Arcane Amphisbaena (-1.3); Carnivorous Cultivator // Enroot (+2.2); Fblthp, Knows the Way (+1.2); ...
   ...
   costo 6+: Craftwork Crusher (+4.1); Craftwork Crusher (+4.1)
2. BR: win rate attesa 50.6% (giocatore medio), -6.65 punti rispetto alla 1
   terre: 17 = 4 Swamp, 13 Mountain
3. WR: win rate attesa 50.6% (giocatore medio), -6.69 punti rispetto alla 1
```

Tra parentesi la qualità di ogni carta, in punti di win rate rispetto alla media del set. La win
rate attesa assoluta è indicativa (il valutatore sovrastima di circa il 7% le differenze, Fase 4);
le distanze tra build sono la parte utile.

## Bug e correzioni durante la fase

- Pool di Pick Two senza 22 carte in una coppia di colori: nessuna build. Ora si provano anche lo
  splash su ogni coppia e i mazzi a tre colori.
- Build "a tre colori" con un solo simbolo del terzo colore e 1 terra base: erano splash travestiti con
  una base di mana pessima. Ora servono almeno 4 carte per colore e 3 fonti.
- Build alternative con splash di carte mediocri, quasi uguali alla prima: ora lo splash si propone
  solo per carte forti.
- Valori NaN della colonna `splash_colors` di 17Lands nella verifica.

## Limiti

- Lo **splash** è molto più raro che nei mazzi reali (vedi sopra).
- La **base di mana** è un'euristica: non tiene conto di carte che cercano terre o producono mana
  (sono taggate in Fase 5 nel tema "lands", ma non usate qui).
- Le **sinergie** non entrano nel punteggio di default (Fase 5); `build --synergy` le include.
- Niente sideboard e niente consigli per il best-of-3 (i formati supportati sono best-of-1).

## Test

`tests/test_build.py`: build a due colori con 17 terre e base proporzionali ai simboli, splash solo di
carte con un simbolo e con almeno 3 fonti, regola delle 16/17 terre, nessuna build senza abbastanza
carte giocabili, tre colori solo con abbastanza carte per colore.
