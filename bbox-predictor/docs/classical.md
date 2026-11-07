# Detector clàssic i refinament geomètric

Hi ha tres opcions compatibles amb el JSON del repte:

| Backend | Identificació de les regions | Ajust de coordenades | Necessita pesos YOLO |
|---|---|---|---|
| `yolo` | Detector entrenat | Reconstrucció dels retalls | Sí |
| `classical` | Alineació OpenCV amb plantilles de referència anotades | Transformacions de capçalera i peu | No |
| `hybrid` | Detector YOLO | Ajust conservador de les vores X a línies visibles | Sí |

El backend clàssic no utilitza xarxes neuronals, OCR ni pesos descarregats. Sí necessita exemples de referència anotats per saber quina regió correspon a cada classe. Les referències es preparen amb els documents de **train**. Les anotacions de la imatge que es prediu no es consulten.

## Instal·lació sense PyTorch

En un entorn virtual nou:

```bash
python3 -m venv .venv-cv
source .venv-cv/bin/activate
python -m pip install -r requirements-cv.txt
```

Aquest entorn només necessita OpenCV, NumPy, Pillow i libvips. Si utilitzes l'entorn YOLO existent, ja té OpenCV; no cal instal·lar-hi la variant headless addicional.

## Preparar les referències

```bash
python prepare_dataset.py --audit-only --output artifacts/cv_dataset
python tools/build_templates.py --manifest artifacts/cv_dataset/manifest.json --output artifacts/templates
```

L'auditoria crea el mateix split per família, però no exporta els 724 retalls d'entrenament. La construcció del banc llegeix només les imatges i anotacions de train i desa característiques ORB i coordenades de referència. ORB i RANSAC són mètodes clàssics d'OpenCV; no s'ajusta cap xarxa ni s'utilitza la GPU.

El banc és portable: `bank.json` i els seus fitxers de característiques es poden copiar junts a un altre ordinador. La predicció no necessita accés als TIFF o JSON originals de les referències.

## Predicció clàssica

```bash
python predict_bboxes.py --backend classical \
  --templates artifacts/templates --image /ruta/document.tif \
  --output artifacts/cv_prediction.json --diagnostics artifacts/cv_diagnostics.json
```

El detector compara l'estructura de la capçalera amb les referències. El final de les columnes s'obté primer amb una alineació independent del peu, perquè l'altura dels documents varia molt. Si el peu no es pot alinear, segueix les dues vores verticals projectades fins al seu últim tram continuat; aquesta alternativa només s'accepta si totes dues vores tenen suport sostingut. Només exporta una predicció completa quan les correspondències són prou consistents. Els formats desconeguts o ambigus produeixen un error explícit; els diagnòstics permeten veure el motiu i les regions parcials disponibles.

Les plantilles no són una solució universal per a documents nous: la cobertura i la precisió es mesuren sobre famílies reservades. Predir correctament una imatge que ja s'ha utilitzat com a referència no demostra generalització.

## Predicció híbrida

```bash
python predict_bboxes.py --backend hybrid \
  --weights /ruta/best.pt --image /ruta/document.tif \
  --output artifacts/hybrid_prediction.json --diagnostics artifacts/hybrid_diagnostics.json
```

Primer s'executa YOLO. Després es busquen línies verticals a resolució original en diverses franges de cada columna. Un ajust necessita suport consistent en diferents franges i queda limitat per la menor distància entre 12 píxels i el 15% de l'amplada de la columna. Si l'evidència és insuficient o ambigua, es conserva la caixa inicial.

Aquest primer refinament modifica només les vores X de les columnes. Conserva els límits Y i la llegenda: un final de color o de textura no és necessàriament el final anotat, i alguns marges són intencionats. El refinament és una opció explícita; `yolo` continua sent el backend per defecte.

## Comparar sobre documents reservats

Sense pesos YOLO, pots mesurar la cobertura del sistema clàssic:

```bash
python tools/benchmark_bboxes.py --manifest artifacts/cv_dataset/manifest.json \
  --templates artifacts/templates --output artifacts/cv_benchmark
```

Per comparar els tres sistemes afegeix els pesos entrenats:

```bash
python tools/benchmark_bboxes.py --manifest artifacts/cv_dataset/manifest.json \
  --templates artifacts/templates --weights /ruta/best.pt \
  --output artifacts/three_way_benchmark --device 0
```

La comparació conserva els documents fallits al denominador amb IoU zero, comprova que les referències no incloguin imatges o famílies de validació, i desa informes i comparacions visuals. El sistema híbrid reutilitza les prediccions de YOLO del mateix benchmark perquè la comparació de coordenades sigui aparellada. El resum també indica quines caixes milloren i quines empitjoren; el temps híbrid inclou el temps de YOLO més el refinament.

No cal reentrenar YOLO per provar aquest ajust. Compara la fracció de documents amb les tres IoU ≥ 0,8, els errors de vores i els casos que empitjoren abans de triar-lo com a predicció final.

## Colab

[classical_colab.ipynb](../notebooks/classical_colab.ipynb) prepara les referències i executa el benchmark en CPU, sense entrenar YOLO. Pots proporcionar-hi després la ruta dels pesos que ja hagis entrenat per afegir la comparació híbrida.

Si ja tens el dataset i un entrenament obert a Colab, utilitza l'actualització de codi `artifacts/opencv_update.zip`: conté els scripts i notebooks, sense tornar a incloure les imatges. Extreu-la al directori del projecte i executa les funcions noves en una cel·la; no cal aturar l'entrenament per descarregar el codi. Executa el benchmark quan la GPU estigui disponible si vols comparar YOLO.
