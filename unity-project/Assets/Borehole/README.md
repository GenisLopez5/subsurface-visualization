# Lithology visualizer

Open **Tools → Lithology → Open CSV Borehole Visualizer**, then Play. The document and cylinder fill their panes, separated by one divider. Local CSV/image loading and zoom controls remain in compact toolbars.

## Controls

- Wheel or drag vertically in either viewport to navigate. Ctrl+wheel or +/− zooms both views. The camera angle is fixed; there is no interactive tilt or rotation.
- Click a cylinder section or its label to select it. Scrolling and zooming preserve that selection.
- **Fit** fits the entire document and the full logged cylinder. Zooming out can reduce the document width. The highlight window appears only when the document fills the pane width and the mapped cylinder range is smaller than the viewport.
- Scroll above the mapped log into the document's legend/header to keep the camera at the surface. A real 3D cutaway ground block surrounds the unchanged cylinder, with its front removed to expose the interior. A camera skybox supplies the sky; there are no 2D earth/sky overlays.
- **CSV** opens a read-only table over the lower 50% of the document viewport; **Close** hides it. It never changes the document viewport dimensions. All original columns and source row order are retained; geometry is independently sorted by depth. The table scrolls and can be moved horizontally independently of both viewports. Clicking a row selects and centers its interval in the views. Clicking a cylinder section selects and reveals the corresponding CSV row. Scrolling or zooming does not change table selection or its scroll position. Synthetic no-data gaps have no CSV row.

## Data

Required CSV columns: `top_depth`, `bottom_depth`, `lithology`; optional paired `top_y`, `bottom_y` enable piecewise depth/image mapping. Feet increase downward. Internal gaps remain no-data geometry, overlaps reject the import, and raw unknown labels retain their fallback material. No aliases are guessed. Limits: 5,000 intervals / 10 MB CSV.

The supplied Allegheny CSV contains 131 intervals at 7,607.9–14,018.1 ft. Its pixel coordinates reach 24,270, while the supplied PNG is only 425×6,736; the user confirmed that this is a different preview. It remains visibly unlinked until the matching TIFF is loaded. Production alignment and header navigation require that matching document; no resize factor has been guessed.

Windows image loading supports single-page TIFF, PNG and JPEG via Windows Imaging Component. Original image dimensions remain separate from GPU texture dimensions so reducing a tall image does not change mapping. Files up to 512 MB are supported; GPU textures are limited to the hardware maximum (up to 16,384) and 32 megapixels. Other platforms may supply already decoded TIFF textures through the API.

```csharp
viewer.SetAnalysisResult(csvText, segmentedTexture, "Analysis result");
// When a preview texture has been reduced, supply both original dimensions:
viewer.SetAnalysisResult(csvText, previewTexture, "Analysis result", originalWidth, originalHeight);
viewer.SetDepth(10000);
viewer.Zoom(0.75);
```

API textures stay caller-owned. Locally decoded images, render targets and generated ground materials are viewer-owned. CSV replacement clears stale images. Invalid CSV retains the previous result. Runtime and Editor code remain separate assemblies. Column geometry and catalogue material generation are unchanged by this iteration; geometry retains a schematic 24-unit total height and proportional interval thickness.

The file-options sidebar, backend requests, database and batch processing remain outside this milestone. The visual palette follows [Viridien](https://www.viridiengroup.com/).

## Verification

Checklist: `IterationTodo.md`. Current verification uses `AgentScripts/VerifyIteration.cs` and artifacts under `Generated/Verification/Iteration`. The `Sync` folder and earlier verification helpers record previous interaction behavior, including the now-removed rotation/automatic selection rules. Run **Borehole.Tests** for parsing, geometry, pixel mapping, TIFF decoding and raw table-data retention; **Lithology.Tests** remains the material regression suite.

Restore point for this iteration: `Temp/IterationRestore/Before-space-navigation.zip`.

## Selection, labels and starting depth

Selecting a section updates the CSV selection without opening the table. The CSV button alone controls visibility. At overview zoom or excessive label density, all section labels hide together with a zoom-in hint; readable labels alternate left and right. A nonzero starting depth has a fixed three-display-unit break between the surface and the first logged interval, labeled 0 → starting depth / not to scale. The break is omitted at zero, and the logged cylinder positions/depth mapping are unchanged. Surface inspection uses a wider context view; its document highlight is hidden.

Material audit: 94 of 131 Allegheny intervals have unmatched source labels (Claystone Silty Clay: 55; Gypsum/Anhyd: 25; Claystone Sililicif: 9; Coal Dolomite: 5). They correctly share the unknown fallback. Salt (K-Na-Mg), 20 intervals, is a recognized generic class with a schematic recipe. Chert, Dolomite and Marl (17 intervals total) resolve to physical recipes. No unverified aliases or mineral mixtures were inferred. See Generated/Verification/Fixes/MaterialAudit.json.

## Final visual refinements

The column pane now takes approximately 37.2% of the workspace (20% smaller than its previous 46.5% share); the column axis is centered. The cutaway ground extends across the underground viewport, avoiding a right-side sky strip while retaining the sky above the surface. Visible labels have horizontal connector lines ending at the cylinder edges, and those lines hide with the labels at overview zoom. A green document band marks the selected interval's exact top_y/bottom_y, independently of the purple navigation window. It updates after cylinder/label/table selection and follows image zoom and pan. It remains visible at Fit when the selected range is onscreen. Missing coordinates, synthetic gaps, missing images and incompatible image sizes never receive an invented band.

## Stable tag anchors

Each section keeps its side permanently (based on section index), even when earlier tags leave the view. Unselected tags are projected from the true section midpoint and naturally scroll out of view; they no longer move to the midpoint of the visible fragment. Only the selected tag follows the visible portion of its section. If its section leaves the viewport, the tag stays at the nearest viewport edge with a connector toward the offscreen section and an up/down cue. Deselection restores the true midpoint anchor. The selected tag stays visible at overview zoom; density hiding applies to other tags.
