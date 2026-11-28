# Fase 5 — Sinergie

Data: 2026-11-28. Sviluppo e verifica su **FRA**, con i modelli della Fase 4 stimati fino al 2026-10-13.

## In sintesi

- Le sinergie tra temi **esistono e si misurano**. Il guadagno di vita, le magie istantanee e
  stregonerie e la densità di Empower Jace rendono di più con più carte di supporto, con effetti
  stabili prima e dopo il taglio.
- Sono però **piccole e sovrastimate dalla regressione**. Fuori campione la win rate reale si muove
  di circa 0,4 punti per ogni punto di sinergia prevista (0,36 ± 0,03 in Premier, 0,23 ± 0,06 in Pick Two).
- **Messe nel punteggio dei pick peggiorano l'accordo con i giocatori forti** (Premier top-1 da
  50,8% a 47,2%). Per questo restano **fuori dal punteggio di default**: `recommend` mostra i temi
  di ogni carta come spiegazione, e `--synergy` le attiva in via sperimentale.

## Tag dei temi

`python -m draftbot tag-set <SET>` scrive `sets/<SET>.tags.yaml`. Il file è **da rivedere a mano**
(`reviewed: false`) e non viene mai sovrascritto: una nuova generazione va in `<SET>.tags.new.yaml`.

Fonti dei tag:
1. **temi generici di Magic** in `tags/themes.yaml` (regole, nessun dato di set): token, contatori +1/+1,
   magie, cimitero, guadagno di vita, sacrificio, terre, artefatti, incantesimi. Per ognuno: carte
   che forniscono il tema (enabler) e carte che ne beneficiano (payoff), riconosciute con regex sul
   testo Oracle senza il testo di richiamo;
2. **temi automatici del set**:
   - keyword non sempreverdi presenti in almeno 6 carte (es. FRA: Empower Jace, Prepared; TRK:
     Station, Beam me up);
   - tipi citati nei testi (es. FRA: "if you control a planeswalker"; TRK: Officer, Spacecraft).
3. Un tema automatico con quasi gli stessi payoff di uno generico viene scartato. Erano stati
   generati entrambi i temi degli artefatti e la regressione li stimava uno contro l'altro (+8,9 e
   −5,4): tenere solo quello positivo gonfiava l'effetto. **Bug trovato e corretto in questa fase.**

**Sui "tag LLM" del piano:** in questo ambiente non c'è accesso a un'API di modelli, quindi i tag
vengono da regole sul testo. La revisione a mano prevista dal piano resta da fare (vedi "Da fare").

## Misura

`python -m draftbot synergy <SET> --format <f>` (`draftbot/synergy/measure.py`):

- Residuo della win rate di ogni mazzo rispetto al valutatore della Fase 4 (qualità delle carte,
  curva, colori, con il livello del giocatore come controllo). La qualità delle carte contiene già
  l'effetto medio di ogni carta: la sinergia è ciò che resta.
- Caratteristiche per tema: enabler e payoff come effetti principali, interazione enabler × payoff / 10;
  per le keyword, la densità m(m−1)/20.
- Ridge pesata (pesi = partite), stima fino al giorno di taglio. Si tiene un tema se l'interazione
  è > 2 errori standard prima del taglio e > 0 dopo.
- **Calibrazione:** la pendenza del residuo reale sulla sinergia prevista, sui mazzi dopo il taglio.
  Gli effetti si usano moltiplicati per questa pendenza.

### Risultati su FRA (taglio al 60% dei giorni: stima fino al 2026-10-19)

| Tema | Premier prima | Premier dopo | Pick Two prima | Pick Two dopo |
|---|---|---|---|---|
| guadagno di vita (interazione) | **+1,34 ± 0,26** | +1,63 | **+2,92 ± 0,61** | +1,02 |
| magie (interazione) | **+1,29 ± 0,24** | +1,78 | **+1,17 ± 0,50** | +0,79 |
| Empower Jace (densità) | **+0,92 ± 0,21** | +0,89 | −0,23 ± 0,49 | +0,17 |
| contatori, sacrificio, cimitero, token | tra −0,2 e +0,2, non significativi | | | |
| Prepared (densità) | −0,93 ± 0,39 | −1,10 | −1,29 ± 0,83 | +0,88 |
| pendenza fuori campione | | **0,36 ± 0,03** | | **0,23 ± 0,06** |

Punti di win rate per unità di interazione. Esempio: 5 enabler e 2 payoff di guadagno di vita fanno 1 unità.

- Una pendenza molto lontana da 0 vuol dire che l'effetto è reale. Il fatto che sia molto sotto 1
  vuol dire che la ridge sovrastima di circa 3 volte: con circa 4 partite per mazzo la stima di
  ogni tema è rumorosa e la selezione dei temi significativi ne gonfia il valore.
- Prepared ha un segno negativo e stabile in Premier. Può voler dire che i mazzi con tante carte
  "prepare" rendono meno di quanto dica la qualità delle singole carte, oppure che il tag è
  impreciso. Va controllato nella revisione dei tag.

### Le sinergie nel pick (backtest della Fase 4, stessi 300 draft)

| | Premier top-1 | Premier top-3 | Pick Two top-1 | Pick Two top-3 |
|---|---|---|---|---|
| motore | **50,8%** | **81,2%** | **36,4%** | **57,4%** |
| motore + sinergie | 47,2% | 78,6% | 35,3% | 55,4% |

La cifra di Pick Two con le sinergie viene dalla corsa prima della correzione dei temi duplicati.
In Premier, prima della correzione, il top-1 con le sinergie era 44,5%.

Il peggioramento resta anche dopo la correzione. Le ragioni più probabili:
- **la sinergia attesa dai pick futuri** (frequenza del tema × carte future) sposta il valore di
  tutte le carte di un tema, anche quando il mazzo finale non lo userà;
- **le probabilità degli archetipi** sono stimate senza sinergie, e aggiungerle le altera;
- **i giocatori forti a loro volta non sembrano draftare "per sinergia"** più di quanto la qualità
  delle carte già rifletta.

Le sinergie quindi **non entrano nel punteggio di default** né del pick né delle build. Sono
mostrate come informazione e attivabili con `--synergy`.

## Analisi per archetipo (aperta dalla Fase 3)

La forza di una carta per coppia di colori era risultata quasi uguale a quella complessiva (τ ≈ 0,3
punti). Le sinergie per tema non cambiano la conclusione: con questi volumi la differenza di una
carta tra archetipi non si misura in modo affidabile. Nel motore la qualità resta unica per carta.

## File

- `tags/themes.yaml`: regole generiche dei temi;
- `sets/<SET>.tags.yaml`: tag di FRA, TRK, HOB, MSH (generati, **da rivedere**);
- `priors/FRA_<formato>_synergy.yaml`: effetti misurati e calibrazione;
- `draftbot/synergy/`: `tags.py`, `measure.py`, `runtime.py`.

## Da fare

- **Revisione a mano dei tag** (soprattutto TRK, che non ha game data per misurarli): togliere i falsi
  positivi delle regex e aggiungere i temi mancanti.
- Quando 17Lands pubblica i game data di TRK: `python -m draftbot synergy TRK --format premier`.
- Possibile miglioramento: sinergie solo nella costruzione finale del mazzo (dove i temi sono certi)
  e non nella proiezione a metà draft. Non provato.

## Test

`tests/test_synergy.py`: temi generici e automatici riconosciuti (testo di richiamo escluso),
caratteristiche di interazione e densità, valore della sinergia che cresce con gli enabler, uso
della calibrazione e nessuna sinergia con pendenza negativa.
