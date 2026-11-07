# Llegendes: reconstrucció completa i refinadors de bandes

## Resultat mesurat

Sobre els 10 documents de desenvolupament, reutilitzant els mateixos pesos:

| Estratègia | IoU mitjana | Llegendes IoU ≥ 0,8 | Llegendes IoU ≥ 0,9 |
|---|---:|---:|---:|
| Detector general amb Y compartida | 0,5119 | 0/10 | 0/10 |
| Especialista individual | 0,4738 | 0/10 | 0/10 |
| Unió anterior de fragments | 0,6358 | 2/10 | 2/10 |
| Millor unió sense marc | 0,8058 | 5/10 | 4/10 |
| Fragments + marc + fila de títols | **0,9433** | **9/10** | **9/10** |

La inferència real amb el checkpoint original reprodueix les prediccions del
benchmark sense reutilitzar deteccions. Les dues columnes no es modifiquen i
9/10 documents tenen simultàniament les tres regions amb IoU ≥ 0,8.
Freeman, una llegenda sense marc, queda parcial amb IoU **0,779**.
No s'han entrenat pesos nous: el guany prové de reconstrucció geomètrica.

La configuració seleccionada es distribueix a `docs/legend_policy.json`:
estratègia `fragments_frame`, confiança del detector 0,05, confiança relativa
de fragments 0,30, IoU vertical 0,60 i separació horitzontal màxima del 5%.
El marc es recupera en una banda local de tota l'amplada, comprovant suport
de línies i contorns, inclosos marcs arrodonits i la fila superior del títol.
Si no hi ha marc fiable o hi ha marcs ambigus, es conserva la caixa anterior.
La predicció no utilitza anotacions, formats revisats ni regles per nom de fitxer.

La mesura local en CPU de l'especialista i la reconstrucció és **15,8 s/document**
de mitjana, excloent la càrrega del model i el detector general de columnes.
La geometria sola, sense memòria cau de marcs, és aproximadament **4,3 s/document**.
Els informes guarden temps i RSS per document; el cost de llegir TIFFs varia
amb la compressió i la mida dels strips. No són temps de la pipeline completa.

Els 10 documents s'han utilitzat per desenvolupar i seleccionar el canvi:
és un benchmark de desenvolupament, no un test independent ni una garantia
per a formats nous.

## Predir amb la configuració seleccionada

Des de l'arrel de `bbox-predictor/`:

```bash
python predict_bboxes.py \
  --image document.tif --weights general_best.pt \
  --legend-weights legend_best.pt \
  --legend-config docs/legend_policy.json \
  --output document.json --device cpu
```

`general_best.pt` és el detector de les tres classes; `legend_best.pt` és
l'especialista d'una sola classe ja entrenat. Si el detector general només
resol les columnes, l'especialista encara pot completar la llegenda.
Per reutilitzar columnes existents, substitueix `--weights` per
`--base-prediction document_base.json`. Aquest JSON ha de correspondre a la
mateixa imatge i dimensions; les coordenades de les dues columnes es conserven.
Si la llegenda continua absent, el CLI acaba amb error sense escriure un JSON nou.

El contracte continua sent `image_name`, `width`, `height`, `bounding_boxes`,
amb les tres classes, coordenades enteres originals i màxims exclusius.
`--diagnostics details.json` explica grups de fragments, marcs, banda i temps.
`--legend-config` és explícit: el predictor antic continua disponible sense
els flags de llegenda.

## Reproduir l'auditoria i el benchmark

```bash
python tools/audit_legends.py --output artifacts/legend_optimization
python tools/optimize_legends.py \
  --manifest artifacts/legend_optimization/audit/manifest.json \
  --base-predictions artifacts/three_way_from_training_report/yolo/predictions \
  --specialist-predictions artifacts/legend_specialist_review/replay/predictions \
  --formats docs/legend_review.json \
  --output artifacts/legend_optimization/final
```

L'auditoria regenera rutes locals del manifest, crea una galeria HTML i retalls
de les 51 llegendes sense carregar documents sencers en memòria. La revisió
visual està registrada a `docs/legend_review.json`, amb hash de cada anotació,
format ample/compacte i marc rectangular/arrodonit/absent. No s'ha trobat cap
error prou clar per canviar una anotació: totes les originals es conserven.

El benchmark compara els tres referents, sis configuracions de fragments,
marc individual i les sis combinacions. Les deteccions reutilitzades han de
ser de confiança 0,05 i mida 2048, amb diagnòstics dels grups candidats.
La selecció maximitza el nombre de llegendes amb IoU ≥ 0,8 i després la IoU
mitjana; els empats conserven l'estratègia anterior. Les regions absents
compten com a zero. No hi ha selecció per document amb les anotacions.

Es desen `comparison.json`, `selected_policy.json`, informes, errors de vores,
desglossament per format, JSON de prediccions i comparacions visuals de totes
les regressions d'experiments nous a `regressions/`. `--no-previews` omet les
imatges. El temps de la configuració seleccionada es torna a mesurar sense
la memòria cau de marcs.

## Colab i els dos entrenaments opcionals

Obre `notebooks/legend_optimize_colab.ipynb`. Genera primer el bundle amb
`python tools/package_colab.py` i puja'l a Drive, juntament amb
`legend_specialist.zip`. El notebook reutilitza els JSON base del primer run,
executa el benchmark, comprova inferència real i exporta pesos/configuració/codi.

Amb `RUN_BAND_TRAINING=True` executa consecutivament els experiments 1024 i
1536 sobre el mateix dataset de bandes. Es mantenen YOLO11s preentrenat,
AdamW, lr0 0,001, 60 èpoques, paciència 10, batch 4 amb recuperació d'OOM,
seed 42 i `rect=True`. Sense mosaic, flips ni girs. La preparació conserva
proporcions, llegendes completes i split 41/10 per família; afegeix marges
variables i negatius de capçalera/cos sense intersecció amb la llegenda.

Localment s'han preparat **276 mostres: 246 train i 30 val**, però els dos
entrenaments no s'han executat: aquest entorn no té GPU. L'objectiu inicial
de 8/10 ja s'ha superat amb la geometria. El resultat de preparació i la
configuració dels experiments es guarden a
`artifacts/legend_optimization/bands/experiment_status.json`.

També es poden executar per CLI:

```bash
python tools/train_legend_bands.py \
  --manifest artifacts/legend_optimization/audit/manifest.json \
  --base-predictions artifacts/three_way_from_training_report/yolo/predictions \
  --specialist-predictions artifacts/legend_specialist_review/replay/predictions \
  --policy docs/legend_policy.json --device 0
```

`--prepare-only` genera les dades sense entrenar. La validació final deriva les
bandes de prediccions, mai de les caixes anotades. Només es selecciona un
refinador si supera la reconstrucció existent. Per utilitzar-lo a inferència,
afegeix `--legend-band-weights band_best.pt --legend-band-imgsz 1024` (o 1536,
segons el run seleccionat). Aquests pesos són diferents dels de l'especialista.
