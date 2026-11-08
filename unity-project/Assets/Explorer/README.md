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
