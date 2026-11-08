# Resum de la sessió: detecció de bounding boxes

## Objectiu

Partint d'un TIFF complet, volem obtenir tres caixes:

- `depth_column`
- `interpreted_lithology_column`
- `lithology_legend`

Les coordenades finals són píxels de la imatge original en format
`[xmin, ymin, xmax, ymax]`, amb els màxims exclusius.

## Resultat del primer model YOLO

Es va entrenar el model general amb YOLO11s. L'entrenament es va aturar per
`early stopping` a l'època 37 i el millor checkpoint correspon aproximadament
a l'època 27.

La reavaluació dels JSON existents, aplicant el límit vertical compartit de les
columnes, va donar:

| Regió | IoU mitjana | IoU p10 | Absents |
|---|---:|---:|---:|
| Profunditat | 0,963 | 0,944 | 0% |
| Litologia interpretada | 0,944 | 0,913 | 0% |
| Llegenda | 0,512 | 0,000 | 20% |

Cap dels 10 documents de validació tenia simultàniament les tres regions amb
IoU ≥ 0,8. La conclusió és que les columnes ja funcionen bé i que la llegenda
és el coll d'ampolla.

## Límits Y compartits

Les 51 anotacions tenen el mateix `ymin` i `ymax` per a les dues columnes.
Això significa que comencen i acaben a la mateixa altura, encara que les
coordenades X siguin independents.

El postprocessament implementat conserva les X de cada columna i aplica:

- `ymin = min(ymin_profunditat, ymin_litologia)`
- `ymax = max(ymax_profunditat, ymax_litologia)`

Aquest canvi està implementat a `bbox_detector/inference.py`.

## Prova amb OpenCV clàssic

Es va implementar una alternativa basada en plantilles, alineació ORB,
morfologia i detecció de línies. També es va preparar un backend híbrid
YOLO + OpenCV.

La primera validació clàssica no va resoldre les columnes completes en cap dels
10 documents reservats. Les plantilles poden ajudar en alguns formats, però la
variació entre documents fa que OpenCV pur no sigui una substitució fiable del
model YOLO. Per tant:

- YOLO continua sent la base.
- L'ajust OpenCV només s'hauria d'acceptar quan millori mètriques reservades.
- La correcció X de les columnes no és prioritària.

La documentació d'aquesta prova és a `docs/classical.md` i el notebook és
`notebooks/classical_colab.ipynb`.

## Nova estratègia per a la llegenda

S'ha separat el problema en dos detectors:

1. El model general existent proporciona les dues columnes.
2. Un model YOLO d'una sola classe detecta només `lithology_legend`.

El detector especialitzat:

- Processa retalls quadrats de tota l'amplada a 2.048 px.
- Busca només abans del començament compartit de les columnes.
- Genera quatre variants positives per document d'entrenament.
- Inclou mostres negatives sense llegenda.
- Manté el split per família: 41 documents de train i 10 de validació.
- Prova confidències 0,05, 0,10 i 0,15.
- Si no troba una llegenda, conserva la predicció del model general.

Fitxers principals:

- `bbox_detector/legend_data.py`: creació del dataset.
- `bbox_detector/legend_training.py`: entrenament d'una classe.
- `bbox_detector/legend_inference.py`: inferència i fusió.
- `tools/train_legend.py`: comanda d'entrenament.
- `notebooks/legend_colab.ipynb`: flux complet de Colab.
- `docs/legend.md`: instruccions curtes.

## Què fa el notebook nou

`notebooks/legend_colab.ipynb`:

1. Munta Google Drive i extreu el projecte.
2. Audita el dataset i reprodueix el split.
3. Genera i visualitza retalls positius i negatius.
4. Entrena YOLO11s només per a la llegenda.
5. Reutilitza els JSON del run `20261107-151404`.
6. Aplica els límits Y compartits a les columnes.
7. Compara el baseline amb les tres confidències del nou detector.
8. Tria primer pel nombre de documents amb les tres IoU ≥ 0,8 i després per
   IoU mitjana de llegenda.
9. Mostra una taula abans/després per document.
10. Exporta pesos, informe, comparacions visuals i codi.

Els resultats es guarden a:

```text
MyDrive/subsurface-bboxes/legend-runs/<data-hora>/
```

## Error trobat a Colab

Colab va mostrar:

```text
ModuleNotFoundError: No module named 'bbox_detector.legend_data'
```

No era una dependència externa: la sessió estava utilitzant una còpia antiga
del projecte o un `colab_bundle.zip` anterior.

La cel·la d'instal·lació del notebook s'ha reforçat perquè:

- Esborri el directori temporal anterior.
- Comprovi que `bbox_detector/legend_data.py` existeix dins del ZIP.
- Elimini de memòria els mòduls antics de `bbox_detector`.
- Mostri la ruta exacta del mòdul importat.

Cal tornar a generar i pujar el paquet després d'aquest canvi:

```bash
python3 tools/package_colab.py
```

Després s'ha de substituir a Drive:

```text
MyDrive/subsurface-bboxes/colab_bundle.zip
```

## Organització del repositori

El projecte s'ha reorganitzat així:

- `bbox_detector/`: lògica reutilitzable.
- `notebooks/`: notebooks de Colab.
- `docs/`: documentació.
- `tools/`: empaquetat, entrenament especialitzat i benchmarks.
- `tests/`: proves automatitzades.
- Arrel: CLIs principals de preparació, entrenament, predicció i avaluació.

Els antics `train_colab.ipynb` i `package_colab.py` de l'arrel s'han mogut a
`notebooks/` i `tools/`, respectivament.

## Validació del codi

La suite completa finalitzava correctament en el moment de preparar el
notebook:

```text
103 passed
```

També es va validar la sintaxi de totes les cel·les i que el paquet inclogués
els mòduls de llegenda.

Aquest era l'estat abans de rebre els pesos entrenats. La revisió posterior
descrita a continuació substitueix els passos pendents d'entrenament.

## Revisió posterior de `legend_specialist.zip` (2026-11-07)

S'ha inspeccionat l'arxiu de Downloads sense sobreescriure codi: els mòduls
Python exportats coincidien amb els del repositori. Els pesos i informes
s'han guardat a `artifacts/legend_specialist_review/imported/`.

La reproducció CPU amb els TIFF originals i les prediccions base coincideix
exactament amb l'informe exportat: IoU mitjana de llegenda **0,473773**, davant
**0,511889** del baseline. Ja no falten llegendes, però continuen sent **0/10**
els documents amb les tres regions a IoU ≥ 0,8. Els tres llindars exportats
produïen els mateixos resultats. El notebook anterior escollia sempre una
confiança de l'especialista, fins i tot si totes empitjoraven el baseline.

S'ha corregit aquesta selecció: ara el baseline també participa, i guanya els
empats. La comparació comprova que es fan servir els mateixos documents i el
mateix llindar d'IoU. El notebook permet reutilitzar pesos existents amb
`EXISTING_LEGEND_WEIGHTS`, sense entrenar ni generar retalls.

Els diagnòstics i les comparacions mostren que el detector fragmenta algunes
llegendes en diverses caixes horitzontals. S'ha afegit una reconstrucció
opcional a `bbox_detector/legend_inference.py`: uneix peces connectades de la
mateixa banda vertical, amb filtres de confiança i separació. No requereix
anotacions a inferència ni modifica les columnes.

Amb els mateixos candidats a confiança 0,05, la reconstrucció aconsegueix
**0,635828** d'IoU mitjana de llegenda i **2/10** documents complets. Les IoU
mitjanes de les columnes es mantenen a **0,962950** i **0,944023**. Els dos
documents que passen són els Ardennes. Encara no és prou fidel: en fallen 8/10,
i aquests resultats són de la validació utilitzada per desenvolupar el canvi.

`tools/evaluate_legend.py` compara les tres estratègies, inclou el baseline en
la decisió i pot reutilitzar els JSON de candidats. Resultat local:
`artifacts/legend_specialist_review/reconstruction/comparison.json`.
`docs/legend.md` conté la taula i les instruccions per reproduir-ho.

Després d'aquests canvis, la suite completa passa amb **117 tests**. També
s'han comprovat la sintaxi de les cel·les del notebook i el contingut del
paquet de codi `artifacts/legend_specialist_review/source_bundle.zip`.

El pas següent és inspeccionar les llegendes encara parcials, especialment
XOM/G08037/Critter Creek, abans d'invertir en més entrenaments. No cal tornar
a entrenar les columnes per provar la reconstrucció.

## Implementació posterior: fragments, marc i títols

S'ha implementat el pla d'optimització a `bbox-predictor/`, la ubicació actual
del projecte. Les 51 llegendes s'han revisat visualment i classificat com a
taules amples/compactes i marcs rectangulars/arrodonits/absents, sense canviar
les anotacions originals. La revisió amb hashes és a `docs/legend_review.json`.

El benchmark compara els tres referents, sis unions de fragments, marcs i sis
combinacions. La configuració seleccionada és `combined_c0.30_y0.60`:
confiança absoluta 0,05, relativa 0,30, IoU vertical 0,60, separació màxima 5%,
recuperació del marc i de la fila de títols. Es distribueix a
`docs/legend_policy.json`. Les columnes es conserven exactament.

Resultats sobre els mateixos 10 documents de desenvolupament:

- IoU mitjana de llegenda **0,943346**, davant de 0,635828 del punt de partida.
- **9/10** llegendes amb IoU ≥ 0,8 i **9/10** amb IoU ≥ 0,9.
- **9/10** documents amb les tres regions a IoU ≥ 0,8.
- Cap regressió respecte de la reconstrucció anterior.
- Freeman continua parcial, a IoU aproximadament **0,779**, i no té marc.

Una reproducció amb el checkpoint real, sense reutilitzar deteccions, dona
exactament les mateixes caixes. Mitjana **15,8 s/document** en CPU per a
l'especialista + geometria, excloent el detector general i la càrrega del model.
Els informes també guarden memòria RSS, vores i temps per document.
Les regressions dels experiments descartats tenen comparacions visuals.

La implementació nova es pot executar amb `--legend-weights` i
`--legend-config` al CLI principal, incloent recuperació quan el model general
només resol les columnes. `--base-prediction` reutilitza els JSON existents.
No hi ha dependència d'anotacions ni de noms de fitxer a inferència.

S'han preparat 276 bandes: 246 train i 30 val, mateix split per família.
`tools/train_legend_bands.py` i el notebook nou
`notebooks/legend_optimize_colab.ipynb` executen opcionalment dos entrenaments
rectangulars 1024/1536, consecutius i amb la resta de paràmetres fixats.
No s'han entrenat en local perquè no hi ha GPU; la geometria ja supera la fita
de 8/10. Els pesos lliurats continuen sent els de l'especialista existent.

La suite completa passa amb **138 tests**, s'ha comprovat la sintaxi de tots
els notebooks i s'ha validat el dataset real de bandes. Les instruccions
actuals són a `docs/legend_optimization.md`; els resultats són a
`artifacts/legend_optimization/final/` i la reproducció real a `live/`.
