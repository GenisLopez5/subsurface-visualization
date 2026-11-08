# Iteration verification

Unity 6000.5.9f1 / Windows Editor. All six checklist items implemented. The underlying cylinder mesh and lithology catalogue were retained. The ground is a separate cutaway block and surface mesh; sky is a camera Skybox material. The fixed camera angle is not user-rotatable.

- 29/29 Borehole.Tests passed, including raw CSV column and source-order retention.
- Live UI verification passed: vertical drag/wheel scrolling, no rotation, no automatic selection on scrolling, CSV selection navigates views, cylinder selection reveals the table row, independent CSV wheel scrolling, half-height overlay without viewport resizing, full document fit, conditional window, surface pinning in unmapped document header, and real 3D scene context.
- Runtime PASS log read back with zero Console errors/warnings, then removed from the verification helper.
- `Header3D.png` and `CsvOverlay.png` use an explicitly named synthetic fixture. The provided CSV/image remain incompatible; accurate production alignment still requires the matching TIFF. No arbitrary coordinate scale was guessed.
- Native file parsing and image decoding tests cover Windows Editor; a standalone Player build was not run during this iteration.

Local Pipeline references consulted/reused: commands/scripts.md, commands/build-and-compilation.md, commands/capture.md and commands/editor-lifecycle-and-observability.md; UI Toolkit skill conventions used for UXML/USS.

Restore point: Temp/IterationRestore/Before-space-navigation.zip. Earlier Sync verification scripts describe previous behavior and should not be used as the current interaction acceptance test; use AgentScripts/VerifyIteration.cs.

Cleanup verified: no ground/skybox materials or viewport RenderTextures remained after Play exit. Play mode is stopped, the original Game-view settings are restored, and the background-running override is off. Final Console: zero errors/warnings. Auto-tick was already enabled and remains enabled.
