# Document explorer

Player rendering dependencies are included through `Assets/Borehole/Resources/BoreholeRenderAssets.asset` and its material references. The build preprocessor prepares and validates these assets. Runtime terrain construction does not rely on finding unreferenced shaders, which Unity can strip from standalone builds. A fresh build is required after this fix.

Development Players accept `-borehole-smoke-test` to check terrain, skybox, lithology materials, the render target, and zoom. They log `BOREHOLE_PLAYER_PASS` or `BOREHOLE_PLAYER_FAIL` and exit; this check is absent from release builds.

Open `DocumentExplorer.unity`, or use Tools > Lithology > Open Document Explorer.
It is the first scene in Build Settings and includes the existing visualizer.

The Document explorer component holds the API base URL. The supplied server uses
HTTP; Player Settings permits HTTP with the user's approval.

- Click selects; double-click opens. Shift selects a range; Ctrl toggles an item.
- Right-click a document for Download ZIP and Delete. Right-clicking a selected
  row preserves multiple selection; an unselected row becomes the selection.
  Delete confirms the names, calls the bulk DELETE endpoint, removes completed
  tracking records, and refreshes the list. Failed deletion preserves selection.
- Download all remains in the toolbar; Open and Download selected are context
  interactions rather than toolbar buttons (opening uses double-click).
- Only `done` documents appear in the library. Search uses the server name filter.
- The library includes an offline world overview. Hovering or keyboard-focusing a
  document highlights its TIFF location; documents without coordinates show an
  explicit unavailable state. A small green/purple marker identifies mapped rows.
  The list response should preferably expose
  `location: {"latitude": ..., "longitude": ..., "label": ...}`. The client also
  accepts this object under `stats.location`, `geo_location`, the aliases `lat`,
  `lon`/`lng`, and GeoJSON-order `coordinates: [longitude, latitude]`. Invalid or
  out-of-range coordinates are never plotted. The map is drawn locally and does
  not call a map or geocoding service.
- Upload TIFFs accepts multiple files (512 MB per transfer). The activity panel
  tracks returned IDs and polls status every three seconds, including in the viewer.
  Upload activity is session-only. Closing the idle panel clears finished entries;
  entering the viewer hides the panel and clears finished entries while active jobs
  continue polling. The green chevron collapses or expands active upload details.
- ZIP downloads use the server's single, selected, and all download endpoints.
  Downloads stream to a temporary file beside the destination before replacing it.
- Back preserves the explorer's current search, list, selection, and scroll position.

Opening uses `/api/v1/files/{fid}/result?format=zip`. The archive contains
`*_solved.csv`, `*_solved.tif`, and `*_offset.json`. The loader adds only
`offset_y_px` to CSV image coordinates; the CSV and existing display scale remain
unchanged. ZIP entry paths are never used as extraction destinations. Missing,
ambiguous, oversized, or inconsistent result contents produce an error message.

Verification: the live result archive parsed 181 intervals with a 680 px offset.
The old individual CSV/TIFF download routes are no longer used for opening.

Native dialogs and TIFF decoding currently target Windows. No standalone player
build has been verified in this iteration.

Document detail uses an owned copy of the received TIFF and a bounded cache of visible 1024-pixel tiles. WIC decodes cropped regions on a worker thread; Unity creates their textures on the main thread. At close zoom, tiles contain native source pixels. Wider views use source-region sampling matched to screen resolution. The reduced whole-page texture is only a loading preview, not the detail source. Original pixel coordinates and the legend displacement are preserved.

## Loading investigation (#10)

Measured on Windows with Unity 6000.5.9f1, 8 November 2026. Two existing server results were downloaded without reprocessing or changing stored data. The PDF-origin document is already a TIFF in the viewer API; this experiment does not measure raw PDF rasterization.

| Fixture | Result image | ZIP download, one observation | Median old decode + texture | Median background preview + texture | Old / preview GPU pixels |
| --- | --- | --- | --- | --- | --- |
| Critter Creek PDF-origin, `aa715df0-ee67-47ee-b700-184bac706b7c` | 1275 × 45638, 20,781,682 bytes | 16,337 ms, 20,960,382 bytes | 1295.9 ms | 1003.7 ms | 7,503,872 / 116,736 |
| Short TIFF, `e0971608-6986-4e46-85da-8b2320516f2e` | 1534 × 3008, 249,034 bytes | 878 ms, 298,165 bytes | 246.6 ms | 127.9 ms | 4,614,272 / 2,138,112 |

Three paired trials per fixture, old path first; medians include texture creation. Large-document trials were 1180.6/1085.7, 1303.6/1003.7, 1295.9/885.6 ms (old/new). Short-TIFF trials were 113.3/109.2, 269.4/161.3, 246.6/127.9 ms. These are preliminary local timings, not a controlled throughput benchmark or an end-to-end speedup claim. The large preview has 98.4% fewer GPU pixels, and its measured preparation cost fell about 23%. The full-resolution detail source and native coordinate dimensions are retained and checked by the regression tests.

For the large fixture, the observed result transfer dominates local preview decoding. For both fixtures, the old archive extraction and WIC decoding execute synchronously after one coroutine yield, blocking Unity updates. The new path prepares the archive and a 2048-pixel-edge overview on a worker; only texture creation and viewer construction run on Unity's thread. The loading card reports downloaded MB and the preparation stage. `DOCUMENT_LOAD` logs download, preparation and first usable viewer-frame times for each subsequent real open.

The server's existing records report processing durations of 8 seconds for the short TIFF and 2 seconds for the PDF-origin document. Their stored processing windows are 8.106 and 4.490 seconds, respectively. These are historical aggregate records, not newly measured model/OCR/CSV timings. Upload duration, queue wait, raw PDF conversion, annotation/model execution and CSV-generation cost need a traced server run with raw PDF and TIFF fixtures before this issue can be closed. No server job was rerun in this investigation. First usable-frame instrumentation is present; baseline/new interactive opens remain to be compared on the same network.

To reproduce the decode comparison, place the two result TIFFs in a private fixture directory named `<id>.tif`, set `SUBSURFACE_PERF_FIXTURES` to that directory, and run the EditMode test `RepresentativePreviewKeepsNativeCoordinatesAndReportsDecodeCost`. Without those private fixtures, the benchmark cases skip explicitly. The first validation passed all 56 EditMode tests; a focused rerun passed both benchmark cases and emitted the timings above. Native coordinate dimensions are asserted for both versions. No private documents are committed.
