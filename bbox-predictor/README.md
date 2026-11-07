# Bounding boxes de mudlogs

Entrenament i predicció de `depth_column`, `interpreted_lithology_column` i `lithology_legend` amb YOLO11s. El predictor retorna les caixes en píxels de la imatge original, compatibles amb el notebook del repte.

També hi ha una alternativa **OpenCV sense xarxes neuronals** i un backend **híbrid YOLO + línies**, amb un benchmark compartit. Consulta [la documentació clàssica](docs/classical.md) i el notebook [classical_colab.ipynb](notebooks/classical_colab.ipynb) per executar-los en CPU o comparar-los amb els teus pesos.

La llegenda és el principal coll d'ampolla del model general. El notebook [legend_colab.ipynb](notebooks/legend_colab.ipynb) entrena un detector d'una sola classe sobre capçaleres i el compara amb els JSON de validació existents, sense tornar a entrenar les columnes. Consulta [les instruccions del detector de llegenda](docs/legend.md).

La reconstrucció de fragments i marcs completa les llegendes amb els pesos
existents: **IoU mitjana 0,9433, 9/10 llegendes amb IoU ≥ 0,9** al benchmark de
desenvolupament. Consulta [la pipeline de llegendes](docs/legend_optimization.md)
i [legend_optimize_colab.ipynb](notebooks/legend_optimize_colab.ipynb), que també
prepara els dos entrenaments opcionals sobre bandes.

## Estructura

- `bbox_detector/`: implementació reutilitzable.
- `prepare_dataset.py`, `train_bboxes.py`, `predict_bboxes.py`, `evaluate_bboxes.py`: comandes principals.
- `tools/`: empaquetat, plantilles OpenCV i benchmarks.
- `notebooks/`: notebooks de Colab.
- `docs/`: documentació addicional.
- `tests/`: proves automatitzades.

## Començar a Colab

1. Des d’aquest directori executa `python3 tools/package_colab.py`. Genera `artifacts/colab_bundle.zip` amb codi, notebook i els 51 TIFF anotats. No cal instal·lar PyTorch per empaquetar; sí Pillow per importar les utilitats compartides.
2. Puja el ZIP a **My Drive/subsurface-bboxes/colab_bundle.zip**. Obre [train_colab.ipynb](notebooks/train_colab.ipynb) a Google Colab i activa una GPU T4 o superior.
3. Executa les cel·les en ordre. Els retalls es preparen al disc local de Colab; pesos, logs i validació es guarden a Drive sota `subsurface-bboxes/runs/<data-hora>/`.
4. Descarrega `inference_export.zip`, generat al final, per executar el predictor localment amb els pesos entrenats.

El ZIP pesa aproximadament 1 GB. Si ja tens les imatges a Colab, pots fer `python3 tools/package_colab.py --source-only` i ajustar `DATASET` al notebook. Les descàrregues de dependències i pesos inicials necessiten internet. La predicció utilitza pesos locals i no crida APIs externes.

Per millorar només la llegenda amb el model general ja entrenat, obre
[legend_colab.ipynb](notebooks/legend_colab.ipynb). Necessita els JSON de
`runs/20261107-151404/validation/predictions/` a Drive i guarda el nou model a
`subsurface-bboxes/legend-runs/<data-hora>/`.

## Entorn local

Utilitza Python 3.10–3.12 en un entorn virtual. Per inferència en CPU:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
```

A Colab conserva la instal·lació CUDA de PyTorch que proporciona l’entorn i instal·la només `requirements.txt`. La versió d’Ultralytics queda fixada; cada entrenament també registra les versions efectives de Python, PyTorch i altres dependències.

## Preparar i entrenar

El dataset es descobreix automàticament sota `hack27-*/hack27/project/dataset` o `dataset/`. Pots indicar qualsevol altra ubicació amb `--dataset`.

```bash
python prepare_dataset.py --audit-only --output artifacts/audit
python prepare_dataset.py --output artifacts/dataset
python train_bboxes.py --data artifacts/dataset/dataset.yaml --output artifacts/training --device 0
```

L’auditoria comprova noms, dimensions i caixes sense descodificar les imatges. Amb les dades actuals i llavor 42: **51 documents, 153 caixes, 29 famílies; 41 documents train i 10 val**. S’exclouen el TIFF duplicat i `Thumbs.db`; l’exemple del repte no s’afegeix com a document independent.

La separació conserva juntes les versions d’un mateix pou i les famílies D582, MDN1, Ardennes, MC822, MC761, MC765, Rio i Critter Creek. Es fa abans de generar retalls. `manifest.json` guarda procedència, grup, split i coordenades de cada retall. Les llistes explícites `train.txt` i `val.txt` eviten que PNG antics entrin a l’entrenament en repetir la preparació amb altres paràmetres. Les rutes del manifest són locals: torna a executar la preparació quan traslladis el dataset a un altre ordinador.

S’exporten com a màxim 16 retalls per document a 1.536 px d’amplada, cobrint llegenda, extrems de columna, interior i negatius. Les columnes que travessen retalls s’etiqueten amb la part visible. Els valors per defecte són 60 èpoques, paciència 10 i batch 4; un error de memòria CUDA provoca reintents amb batch 2 i 1. Es desactiven inversions i mosaic i s’apliquen variacions suaus de llum i escala. `--epochs`, `--batch`, `--imgsz`, `--workers` i `--max-hours` permeten ajustar recursos; el límit d’hores s’aplica a cada intent.

El resum `artifacts/training/training_summary.json` indica el checkpoint efectiu, hash, configuració, mètriques dels retalls i temps. Si hi havia runs anteriors, Ultralytics crea un nou directori; utilitza la ruta `best_weights` del resum.

## Predir una imatge nova

```bash
python predict_bboxes.py \
  --image /ruta/document.tif \
  --weights artifacts/training/detector/weights/best.pt \
  --output artifacts/predictions/document.json \
  --preview-dir artifacts/previews/document \
  --device cpu
```

`--preview-dir` és opcional i no modifica el JSON públic. Genera un overview,
zooms de capçalera/top/middle/bottom/llegenda, una imatge per chunk,
`chunks_contact_sheet.jpg`, `trace.json` i un `index.html`. Les deteccions
seleccionades es pinten en blau/taronja/lila, les descartades en gris, els
reintents en groc, la caixa anterior al shared-Y discontínua i la final en
magenta. Sense aquesta opció no es conserva la traça detallada.

La sortida té aquest contracte:

```json
{
  "image_name": "document.tif",
  "width": 1700,
  "height": 30000,
  "bounding_boxes": {
    "depth_column": [300, 2000, 350, 29000],
    "interpreted_lithology_column": [700, 2000, 780, 29000],
    "lithology_legend": [30, 500, 1650, 1000]
  }
}
```

Les coordenades són enteres `[xmin, ymin, xmax, ymax]`, amb origen superior esquerre i màxims exclusius. Els números de l’exemple només il·lustren el format. No cal proporcionar anotacions al predictor. Es comprova que els pesos continguin exactament les tres classes; els pesos COCO inicials encara no serveixen per predir-les.

La inferència recorre franges quadrades de tota l’amplada amb un 50% de solapament. Agrupa deteccions compatibles en X i trams continus en Y, i estima els extrems amb deteccions interiors als retalls. Els extrems censurats es tornen a processar amb retalls desplaçats. Les X de les dues columnes es reconstrueixen independentment, però comparteixen el menor `ymin` i el major `ymax`: aquesta convenció es compleix exactament en les 51 anotacions. Si una regió continua sense resoldre’s, el procés acaba amb codi 2 i missatge explícit, sense escriure un JSON nou de successos. Fes servir una ruta nova per intent per evitar confondre una sortida antiga amb una predicció fallida.

L'ajust a línies d'OpenCV s'activa explícitament amb `--backend hybrid`. El backend per defecte és `yolo`; compara els resultats amb [el benchmark](docs/classical.md) abans de triar el refinament, perquè podria eliminar marges correctament anotats.

## Avaluar les caixes finals

```bash
python evaluate_bboxes.py \
  --manifest artifacts/dataset/manifest.json \
  --weights artifacts/training/detector/weights/best.pt \
  --output artifacts/validation --device 0
```

L'avaluació crea `visual_review/index.html`, ordenat pel pitjor error de vora Y,
amb zooms que es poden inspeccionar sense dependre de l'overview comprimit. Afegeix
`--chunk-previews` quan s'avalua directament amb pesos per incloure la traça i
les imatges de tots els chunks. Amb `--predictions`, només es poden reconstruir
les caixes finals perquè els JSON antics no contenen les deteccions crues.

Es creen `report.json`, `per_document.csv`, JSON de prediccions i comparacions visuals amb anotació verda i predicció magenta. Les mètriques inclouen IoU per classe (mitjana, mediana i percentil 10), errors absoluts de cada vora, regions absents i percentatge de documents amb **les tres regions amb IoU ≥ 0,8**. Les prediccions absents o fallides continuen al denominador amb IoU zero. L’error de vores només es calcula per parelles presents i es mostra juntament amb el nombre de parelles.

La validació també es pot executar sense model sobre JSON generats anteriorment: substitueix `--weights ...` per `--predictions /ruta/json/`; cada fitxer ha de dir-se `<nom-del-TIFF-sense-extensió>.json`. `--no-previews` permet ometre imatges de comparació.

Les mètriques dels retalls seleccionen `best.pt`; la fidelitat útil per a l’equip es mesura sobre documents reconstruïts. El split actual reserva famílies completes i totes les imatges de validació són RGB: la família D582 conté les nou imatges d’1 bit i queda sencera a entrenament. Cal una comprovació separada deixant aquesta família fora per mesurar generalització a un estil d’1 bit nou. Els documents reservats dels organitzadors continuen sent l’avaluació final independent.

## Memòria i proves

libvips llegeix regions TIFF i les redimensiona abans de crear RGB. Alguns TIFF tenen un únic strip comprimit molt gran: poden necessitar aproximadament **1,5 GB de disc temporal**, que s’allibera en tancar el document. Reserva diversos GB de disc per originals, retalls i temporals. Sense libvips, els escaneigs grans produeixen un error explícit.

La validació registra temps de predicció i RSS del procés mostrejat cada 50 ms, incloent model i comparacions; els pics breus poden no aparèixer. Aquesta mesura no inclou VRAM. La càrrega inicial del model s’exclou del temps per document.

```bash
python -m pip install 'pytest>=8,<10'
python -m pytest -q
```

Els tests comproven conversió i reconstrucció de coordenades, retalls i vores censurades, reintents, TIFF RGB/paleta/1 bit, agrupació i absència de leakage, mètriques amb prediccions absents i errors d’entrenament. Les proves injecten detectors controlats perquè la suite no descarregui pesos ni necessiti GPU.

Referències: [YOLO11](https://docs.ultralytics.com/models/yolo11/), [entrenament](https://docs.ultralytics.com/modes/train/), [validació](https://docs.ultralytics.com/modes/val/) i [lectura per regions amb pyvips](https://libvips.github.io/pyvips/).
