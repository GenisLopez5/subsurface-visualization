# Synchronized viewer verification — 2026-11-07

Unity 6000.5.9f1, URP 17.5.0, Windows Editor. The existing visualizer scene was updated through the Editor API, not by manually editing serialized scene data.

## Results

- **Borehole.Tests: 28/28 passed.** Includes existing 18 parser/geometry cases, piecewise pixel/depth interpolation and inverse mapping, invalid/missing/conflicting pixel coordinates, all 131 supplied CSV rows, native TIFF decoding and pixel orientation, and a 20,000-pixel TIFF reduced for the GPU while retaining original dimensions.
- **Lithology.Tests: 22/22 passed.** Existing shared material regression suite.
- **Live UI verification passed.** Actual wheel, pointer drag and button callbacks exercised. Player loop advanced from frame 4,937 to 5,220 during the last run. Checked cylinder → document scrolling, document → cylinder wheel and drag, orbit, cylinder picking, legend mode, gap navigation, fixed image/overlay width, centered overlay, zoom-dependent window height, minimum zoom and camera-projection agreement, explicit original dimensions for downsampled textures, image-picker adapter plus PNG decoding, and invalid-import retention.
- **Supplied-image mismatch detected.** CSV maximum bottom_y=24,270; supplied PNG=425×6,736. It remains a clearly labeled unlinked preview. The matching TIFF is still pending from the user; alignment against that actual document has not been verified.
- **Console:** no errors or warnings; compilationFailed=false. Temporary PASS log was read back in Play mode, then removed from the verification script.
- **After Play-mode exit:** 0 owned RenderTextures, 0 cylinder meshes, 0 selection materials and 0 decoded/fixture document textures. This check stopped with a locally decoded PNG owned by the viewer, confirming its exit cleanup.

`SynchronizedFixture.png` is an explicitly labeled synthetic matched fixture with source coordinate basis 800×6,000 and a 400×3,000 texture. Its shale/sandstone boundary demonstrates the linked views. It is not an attempted alignment of the user's mismatched files. `ProvidedPreviewMismatch.png` shows the real supplied files and the size mismatch message. The default scene retains the supplied files, not the synthetic test fixture.

## Evidence

- `EditModeResults.json`
- `LithologyRegressionResults.json`
- `LiveResults.json`
- `ConsoleStatus.json`
- `CleanupResults.json`
- `SynchronizedFixture.png`
- `ProvidedPreviewMismatch.png`

## Scope and limits

No file-options sidebar, backend API client, batch processor, database browser or PDF renderer was added. Native TIFF loading was tested in the Windows Editor using single-page uncompressed fixtures, including an oversized page; a standalone Player build and the pending production TIFF were not tested. Windows Imaging Component performs file decoding. Other platforms may supply decoded TIFF textures through the API. Input identity cannot be proven from bounds alone; callers must pair the CSV with its matching image. Ninety-four supplied source labels are unresolved by the existing exact catalogue and retain the explicit schematic fallback.

Play mode is stopped, the original Game-view size/layout is restored, and the temporary Run In Background override is off. Auto-tick was already enabled before this task and remains enabled. Source helpers are in `AgentScripts/`, and new screenshots/results are confined to this Sync verification folder. Restore point: `Temp/SyncRestore/Borehole-before-sync.zip`.

## Documentation consulted

Unity CLI and UI Toolkit skill instructions, plus the installed Pipeline package's `commands/scripts.md`, `commands/build-and-compilation.md`, `commands/capture.md`, and `commands/editor-lifecycle-and-observability.md`. Visual direction inspected at [Viridien](https://www.viridiengroup.com/).
