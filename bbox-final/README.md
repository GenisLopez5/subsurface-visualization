# Bounding boxes: workflow final

Una imatge entra; surt un JSON amb profunditat, litologia interpretada i llegenda.
Els dos models estan inclosos. La pipeline optimitzada de llegenda està activada
per defecte: fragments, marc i reintent amb finestres locals quan falta un marc fiable.

## Executar

Des d'aquesta carpeta, amb les dependències instal·lades:

```bash
python predict.py /ruta/document.tif --output document.json
```

La comanda també imprimeix el JSON a stdout. Per utilitzar només stdout:

```bash
python predict.py /ruta/document.tif
```

La GPU s'utilitza automàticament si està disponible. Per forçar CPU:

```bash
python predict.py /ruta/document.tif --output document.json --device cpu
```

Les rutes de models i configuració es resolen respecte d'aquesta carpeta;
la comanda també funciona des d'un altre directori. No cal el projecte
`bbox-predictor`, cap dataset, anotació, JSON previ ni accés a Drive.

## Instal·lació

Python 3.10–3.12. En local amb CPU:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
```

A Colab conserva el PyTorch de l'entorn i instal·la només `requirements.txt`.
El notebook `run_colab.ipynb` permet pujar el ZIP d'aquesta carpeta, instal·lar
dependències, pujar un document i descarregar el JSON. No entrena cap model.

La inferència utilitza exclusivament pesos locals; no descarrega models ni
crida APIs. Els TIFF molt llargs es llegeixen per regions amb libvips, inclòs
a les dependències. Alguns TIFF d'un sol strip requereixen espai temporal
per a la descompressió.

## Resultat

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

Les coordenades de l'exemple són il·lustratives. El format és
`[xmin, ymin, xmax, ymax]`, enters sobre la imatge original, amb origen superior
esquerre i màxims exclusius. El refinament de llegenda conserva les dues
columnes del detector general, que ja comparteixen límits verticals.

Si el detector general no troba la llegenda, l'especialista pot recuperar-la.
Si falta una columna o la llegenda continua sense resoldre's, la comanda acaba
amb codi 2, explica l'error a stderr i no escriu un JSON de predicció parcial.

## Integració en Python

```python
from bbox_runtime import BoundingBoxPredictor

predictor = BoundingBoxPredictor()
result = predictor.predict("document.tif")
legend = result["bounding_boxes"]["lithology_legend"]
```

Reutilitza la mateixa instància per processar diverses imatges: els models es
carreguen una sola vegada. Per obtenir detalls opcionals:

```bash
python predict.py document.tif --output document.json --diagnostics details.json
```

`details.json` indica models, configuració, reintents i temps d'inferència.
El JSON principal manté només el contracte de les tres regions.

## Contingut

- `predict.py`: entrada per línia de comandes.
- `bbox_runtime/`: implementació d'inferència independent.
- `models/general.pt`, `models/legend.pt`: pesos entrenats inclosos.
- `models/manifest.json`: classes i hashes comprovats en carregar els models.
- `pipeline.json`: configuració final de les dues etapes.
- `run_colab.ipynb`: inferència amb càrrega de fitxer a Colab.
- `tests/`: proves de la integració final.

`source_snapshot.json` registra l'origen del codi utilitzat. No intervé en
la predicció. El paquet no conté entrenaments ni dades anotades.

La configuració de llegenda prové del benchmark de 10 documents de
desenvolupament: IoU mitjana 0,950 i 10/10 llegendes amb IoU ≥ 0,8, però només
6/10 amb IoU ≥ 0,95. No són garanties de fidelitat sobre documents nous.

## Benchmark sobre un test anotat

`benchmark.py` executa inferència nova i seqüencial amb el workflow complet
(detector general + especialista + reconstrucció de llegenda). Accepta
directament el ZIP dels sponsors o una carpeta que contingui `images/` i
`annotations/` (també admet `bbox_annotation/`):

```bash
python benchmark.py /ruta/test.zip --output outputs/sponsor-test
```

El mode `auto` reprodueix la selecció de producció: pesos `.pt` sobre CUDA si
estan complets a `models-pt/`; altrament utilitza els models INT8 empaquetats.
Per fixar explícitament la variant i el dispositiu:

```bash
# INT8/OpenVINO en CPU
python benchmark.py /ruta/test.zip --output outputs/test-int8 \
  --models int8 --device cpu

# Pesos originals en CPU o GPU 0
python benchmark.py /ruta/test.zip --output outputs/test-pt-cpu \
  --models pt --device cpu
python benchmark.py /ruta/test.zip --output outputs/test-pt-gpu \
  --models pt --device 0
```

Els models es carreguen una sola vegada. Per a cada document, l'anotació
s'obre només després d'acabar la inferència de la imatge. Les prediccions
fallides continuen al denominador amb IoU zero. La carpeta de sortida ha de ser
nova o buida i conté:

- `index.html`: resum llegible i taula per document.
- `report.json`: mètriques completes, configuració, versions, hashes i temps.
- `per_document.csv`: IoU i rendiment per document.
- `predictions/`, `diagnostics/`, `errors/` i `ground_truth/`: evidència de cada
  execució.

Es reporten IoU mitjana/mediana/p10 per regió, errors de vores, taxa
d'absències, documents amb les tres IoU ≥ 0,8, temps end-to-end i pic de memòria
RSS. El temps per document exclou la càrrega inicial dels models i l'escriptura
de l'informe. RSS no inclou VRAM. Per comprovar ràpidament la instal·lació sense
executar tot el test, es pot afegir `--limit 1`; aquest resultat no s'ha de
presentar com el benchmark complet.

## Servei HTTP (per a una altra màquina)

`server.py` exposa el mateix workflow amb FastAPI, perquè l'aplicació de litologia el cridi per xarxa:

```bash
./run_server.sh                      # primer cop: crea .venv i instal·la requirements-server.txt (torch CPU)
curl http://<ip>:8100/health
curl -F file=@document.tif http://<ip>:8100/detect
```

`POST /detect` accepta `.tif/.tiff/.png/.jpg` (els PDF els renderitza el client) i retorna el mateix JSON
que `predict.py`, més `seconds`. 422 si no es resolen les tres regions. Variables: `PORT`, `HOST`,
`BBOX_DEVICE` (`auto`, `cpu`, `0`), `BBOX_MAX_UPLOAD_MB`.
