# Diverses finestres després de la primera predicció

## La captura d'Ardennes i les mètriques

La predicció aportada a `Downloads/test/prediction.json` coincideix exactament
amb el detector general: llegenda `[157, 601, 1208, 1008]`, IoU **0,661491**.
L'anotació és `[2, 600, 1538, 1021]`. La pipeline de llegenda optimitzada havia
predit `[2, 599, 1537, 1025]`, IoU **0,987627**, sobre el mateix TIFF original.
La captura per si sola no mostra que s'hagin executat els flags de llegenda.

La referència actual, abans d'aquest experiment, té IoU mitjana **0,943346**
en 10 documents de desenvolupament. El nombre de llegendes que superen cada
llindar és: **9/10 a 0,8; 9/10 a 0,9; 6/10 a 0,95; 2/10 a 0,98**.
Una IoU mitjana de 0,943 no és un percentatge d'encerts ni significa que totes
les vores siguin exactes. Per exigir caixes gairebé exactes cal mirar també
els llindars 0,95/0,98 i els errors de cada vora.

Per reproduir la pipeline ja optimitzada:

```bash
python predict_bboxes.py --image document.tif --weights general_best.pt \
  --legend-weights legend_best.pt --legend-config docs/legend_policy.json \
  --output document.json --diagnostics details.json --preview-dir review
```

`general_best.pt` és el detector de tres classes; `legend_best.pt` és
l'especialista d'una classe. Les dades de `details.json` permeten veure
si s'ha executat l'etapa de llegenda.

## Experiment: finestres i vots de vores

La idea és semblant a la inferència per retalls de
[SAHI](https://obss.github.io/sahi/guides/sliced-inference/): retalls
solapats, prediccions locals i reconstrucció en coordenades originals.
La llegenda és un objecte gran; per això es conserva també una vista global.
Fer retalls més petits pot generar més fragments, i un merge sense controls
pot incorporar taules veïnes.

La implementació experimental:

1. Executa la pipeline actual i comprova el marc geomètric.
2. Si el marc és fiable, conserva la predicció sense més inferències.
3. Si no hi ha marc o és ambigu, llegeix una única banda de tota l'amplada.
4. Processa dues vistes completes amb marges diferents i tres finestres
   horitzontals solapades: esquerra, centre i dreta. Sense flips ni girs.
5. Retorna totes les deteccions a coordenades originals i conserva només
   candidats connectats amb la mateixa banda vertical.
6. Amplia una vora només quan almenys dues vistes diferents hi coincideixen.
   Les vores tallades per un límit intern del retall no voten. Diverses caixes
   d'una sola vista no compten com a diversos suports. Es rebutgen expansions
   excessives i es conserva la primera predicció si no hi ha evidència suficient.

No canvia cap coordenada de les columnes, no consulta anotacions i utilitza
els mateixos pesos. La configuració optimitzada anterior es conserva; la
configuració experimental és `docs/legend_multiview_policy.json`.

Per provar-la amb una imatge, canvia només el valor de `--legend-config` a
`docs/legend_multiview_policy.json` en la comanda anterior.

## Resultat de la prova local

Amb els mateixos pesos i inferència real, Freeman passa de **0,778517** a
**0,845262** d'IoU. Les altres nou prediccions no canvien, ni tampoc les 20
caixes de columnes. La IoU mitjana puja de **0,943346** a **0,950020**.

| Llindar de llegenda | Pipeline anterior | Reintent local |
|---|---:|---:|
| IoU ≥ 0,8 | 9/10 | 10/10 |
| IoU ≥ 0,9 | 9/10 | 9/10 |
| IoU ≥ 0,95 | 6/10 | 6/10 |
| IoU ≥ 0,98 | 2/10 | 2/10 |

Les tres caixes superen IoU 0,8 en 10/10 documents. Això encara no significa
que les caixes siguin exactes: els llindars més exigents no milloren.
El guany prové d'un únic document i del mateix conjunt de desenvolupament.
El següent experiment amb més potencial per afinar vores és el refinador
supervisat de bandes ja preparat a `legend_optimize_colab.ipynb`, avaluat
també amb IoU 0,95/0,98 i errors de vores, i conservant el sistema actual si
el refinador no millora aquests resultats.

Els resultats són a `artifacts/legend_multiview_probe/evaluation/`; la mitjana
mesurada d'aquesta execució és 19,7 s/document en CPU, excloent el detector de
columnes i la càrrega inicial. El reintent afegeix cinc passades només al cas
on el marc no és fiable. Passen 149 proves automatitzades en aquesta sessió.

## Avaluació reproduïble

```bash
python tools/evaluate_legend_multiview.py \
  --manifest artifacts/legend_optimization/audit/manifest.json \
  --base-predictions artifacts/legend_optimization/final/baseline/predictions \
  --weights artifacts/legend_specialist_review/imported/legend_best.pt \
  --reference-report artifacts/legend_optimization/live/report.json \
  --output artifacts/legend_multiview_probe/evaluation --device cpu
```

El resultat és `report.json`, prediccions i `multiview_summary.json`, amb els
quatre llindars, diferències per document i temps. `--previews` genera imatges.
Els temps inclouen l'especialista i els reintents, però no el detector general,
perquè s'utilitzen els JSON de columnes existents. La selecció usa els mateixos
10 documents de desenvolupament; no és una comprovació independent.

En una sessió del notebook d'optimització amb el codi actualitzat, es pot executar
la comparació amb una cel·la addicional després de l'apartat de temps real:

```python
subprocess.run([
    sys.executable, 'tools/evaluate_legend_multiview.py',
    '--manifest', str(MANIFEST),
    '--base-predictions', str(RUN / 'benchmark/baseline/predictions'),
    '--weights', str(LEGEND_WEIGHTS),
    '--reference-report', str(RUN / 'live/report.json'),
    '--output', str(RUN / 'multiview'), '--device', DEVICE,
], check=True)
```

Les proves cobreixen recompte per vista, vores censurades, regions d'una altra
fila, candidats desconnectats, expansions excessives, transformacions de retalls,
absència de deteccions i preservació dels marcs fiables.
