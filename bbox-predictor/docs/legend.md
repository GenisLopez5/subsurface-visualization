# Detector especialitzat de llegenda

El model general ja resol bé `depth_column` i
`interpreted_lithology_column`. Aquest pipeline conserva aquestes prediccions,
comparteix el seu `ymin/ymax` i entrena un segon YOLO d'una sola classe per a
`lithology_legend`.

## Executar a Google Colab

1. Genera el paquet:

   ```bash
   python3 tools/package_colab.py
   ```

2. Puja `artifacts/colab_bundle.zip` a
   `My Drive/subsurface-bboxes/colab_bundle.zip`.
3. Mantén a Drive els JSON de la validació anterior a
   `subsurface-bboxes/runs/20261107-151404/validation/predictions/`.
   També s'accepta `validation_shared_y/predictions/`.
4. Obre `notebooks/legend_colab.ipynb`, activa una GPU T4 o superior i executa
   totes les cel·les en ordre.

No cal tornar a entrenar el detector de columnes. El notebook reprodueix el
split de 41 documents de train i 10 de validació, crea retalls de capçalera,
entrena el model de llegenda i prova confidències 0.05, 0.10 i 0.15.

Per reutilitzar l'especialista ja entrenat, posa la ruta del seu checkpoint a
`EXISTING_LEGEND_WEIGHTS` a la primera cel·la. Això evita l'entrenament i la
generació de retalls. `MERGE_FRAGMENTS=True` activa la reconstrucció de peces
de llegenda; `False` reprodueix la selecció original d'un sol candidat.

## Resultats

Cada execució es guarda a
`My Drive/subsurface-bboxes/legend-runs/<data-hora>/`:

- `training/`: pesos i logs del detector de llegenda.
- `comparison.json`: resum del baseline i de cada confiança.
- `final_best/report.json`: mètriques finals per document.
- `final_best/comparisons/`: anotació verda i predicció magenta.
- `legend_specialist.zip`: pesos, informe i codi necessari per continuar.

El criteri principal és el nombre de documents on les tres regions obtenen
IoU ≥ 0,8. En cas d'empat, es compara la IoU mitjana de llegenda, comptant les
regions absents com a zero. El baseline participa en la selecció i es conserva
si cap candidat el supera estrictament. `best_confidence: null` significa que
s'ha seleccionat el baseline. Si el nou model no detecta una llegenda, la fusió
conserva la predicció del model general.

## Revisió dels pesos entrenats: 7 de novembre de 2026

S'han reproduït localment els resultats de `legend_specialist.zip` amb els pesos
aportats, els TIFF originals i els JSON base, sense entrenar de nou:

| Estratègia | IoU mitjana de llegenda | Llegendes absents | Documents amb les 3 IoU ≥ 0,8 |
|---|---:|---:|---:|
| Baseline amb Y compartida | 0,5119 | 2/10 | 0/10 |
| Especialista, candidat individual | 0,4738 | 0/10 | 0/10 |
| Especialista, unió de fragments alineats | 0,6358 | 0/10 | 2/10 |

Les dues columnes es mantenen iguals: IoU mitjana 0,9629 i 0,9440. Els llindars
0,05, 0,10 i 0,15 de l'informe exportat donaven el mateix resultat individual;
la reconstrucció nova s'ha avaluat localment a 0,05.

En els documents Ardennes, per exemple, el detector retorna quatre peces
horitzontals de la mateixa taula. Escollir-ne només una perd la major part de
l'amplada; unir-les augmenta la IoU de 0,488/0,487 a 0,947/0,953.
La reconstrucció conserva el candidat seleccionat com a referència i afegeix
només peces amb confiança ≥ la meitat de la seva, IoU dels intervals verticals
≥ 0,7 i separació horitzontal ≤ 5% de l'amplada del document. No consulta
anotacions ni imposa una amplada fixa de llegenda.

Encara fallen 8 dels 10 documents segons el criteri complet. Aquest canvi s'ha
desenvolupat amb la mateixa validació: és una millora observada en validació,
no una estimació independent de generalització.

## Comparar sense tornar a entrenar

```bash
python tools/evaluate_legend.py \
  --manifest artifacts/audit/manifest.json \
  --base-predictions artifacts/three_way_from_training_report/yolo/predictions \
  --weights artifacts/legend_specialist_review/imported/legend_best.pt \
  --output artifacts/legend_comparison --device cpu --conf 0.05
```

El script executa l'especialista un cop, compara baseline/candidat/unió, i desa
`comparison.json`, informes i prediccions JSON en coordenades originals.
Amb `--specialist-report artifacts/legend_specialist_review/replay/report.json`
en lloc de `--weights` reutilitza les deteccions i els seus diagnòstics sense
carregar el model. `--save-previews` genera comparacions visuals opcionals.
Els temps dels modes que reutilitzen JSON no inclouen la inferència original.

Resultats d'aquesta revisió:
`artifacts/legend_specialist_review/reconstruction/comparison.json` i
`artifacts/legend_specialist_review/reconstruction/fragments/predictions/`.
