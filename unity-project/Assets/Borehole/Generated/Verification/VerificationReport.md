# CSV borehole verification

Verified in the existing **Unity 6000.5.9f1 / URP 17.5 / DX12** Editor on 2026-11-07. No Unity version, render pipeline, or package dependency was changed for this feature.

- **18/18 Borehole.Tests passed**: supplied 17-interval CSV; exact 25,400–25,700 ft range; column reordering; quoted/escaped fields; BOM; ignored extra columns; sorting; internal gaps; overlaps and malformed input; preserved unknown labels; proportional schematic mapping; outward mesh normals and actual top/bottom caps; shared mesh/material assignment; rebuilding without duplicate generations.
- **22/22 existing Lithology.Tests passed**, including their GPU checks. Results are in `BoreholeEditModeResults.json` and `LithologyRegressionResults.json`.
- **Live verification passed**: player frames advanced; actual wheel and pointer-drag/click events moved, rotated, and selected the cylinder; zoom button handler changed the depth window; catalogue legend mode updated all sections; invalid CSV retained the previous document; the CSV-button picker callback loaded the sample through a deterministic test adapter.
- **Gap and unknown-label display verified**: an internal gap produced a solid unavailable-data material and a “No data” label; an unrecognized lithology retained its original text and an intentional schematic fallback.
- **Backend handoff verified with an image fixture**: `SetAnalysisResult(csv, image)` populated the segmented-document view; switching back preserved depth; importing another CSV cleared the stale image. No real PDF or backend response was supplied, analyzed, or uploaded.
- Live results are in `LiveResults.json`; screenshots are `Viewer.png`, `NoDataGap.png`, and `DocumentView.png`. The temporary verification log was read back from the Unity console. The runtime reported zero errors and zero warnings after the startup fixes.
- Runtime assembly scan found no UnityEditor dependency. UI setup waits for UIDocument initialization; additive-scene isolation occurs after scenes load, and restores the other scene roots on disable. RenderTexture, generated meshes, material leases, and the native filename buffer have explicit cleanup.
- Final Play-mode shutdown check confirmed **0 owned viewport textures, 0 owned cylinder meshes, and 0 owned selection materials**. Editor lifecycle cleanup handles Play-mode exit, script reload, and quitting. See `CleanupResults.json`.

The Editor is left out of Play mode with the new visualizer scene open. The temporary Run In Background override is reverted. Existing scenes remain open and their roots are restored. The original Game-view resolution/layout is restored after the 1440×900 visual check. The pre-change restore point is `Temp/BoreholeRestore/Lithology-before-borehole.zip`.

Not executed: standalone Player build, Windows Player's native common file dialog, mobile/WebGL file selection, real backend/PDF integration, and performance profiling with thousands of intervals. Editor file selection uses Unity's `EditorUtility.OpenFilePanel`; Windows Player selection uses the native common file dialog. The debug input limits are 5,000 source intervals and 10 MB. See `Assets/Borehole/README.md` for controls, supported CSV behavior, scaling and integration API.

Reproducible live verification harness: `AgentScripts/VerifyBorehole.cs` (Editor-only in-memory CLI script, outside Assets). Its button activation helper uses the current UI Toolkit Clickable handler for deterministic testing; that reflection is not part of the shipped runtime.
