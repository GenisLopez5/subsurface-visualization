# Verification — 2026-11-07

Environment: existing interactive Unity **6000.5.9f1**, URP **17.5.0**, **DX12**. Current quality override: `PC_RPAsset`; Graphics default and Mobile override also use URP. No second Editor was opened for this project. Existing SampleScene remained open; the demo was created additively.

- C# import/compilation: passed in the actual Editor. Runtime assembly contains no UnityEditor dependencies.
- Catalogue validation: passed. 180 unique labels, 182 source occurrences, B01–B25, 53 modifiers, 25 null-base opaque codes, no missing generated references.
- Unity EditMode suite: **22 passed, 0 failed, 0 skipped**. Raw results: `EditModeResults.json`.
- GPU tests: zero-strength component is pixel-identical to the host; a nonzero component changes the image. Object-rest patterns remain stable through translation with negative/nonuniform scale.
- Data/behavior tests cover resolver normalization, invalid/unknown input, evidence-gated aliases and ambiguity; duplicate source locations; unapproved name candidates; combined components; optional lamination; unsupported layer/unknown-modifier diagnostics; idempotent initialization, GUID/override/texture preservation; material reuse, bounds, slot isolation/restoration; invalid coordinate fallback; periodic image edges and map import conventions.
- Visual inspection: rendered family gallery, representative recipes, cutaway, legend, and close-up sandstone/coquina/igneous/tuff examples. Generated assets are visibly illustrative. Close-up coquina normals produce raised shell detail. Solid cut surfaces and opaque missing-data states are present. No pink/error materials observed.
- Runtime UI: 180 selector choices; changing the selector updates the specimen; legend and grain controls work. Verified with **domain reload disabled**. Controller advanced from frame **671 to 789** during a timed observation. See `PlayModeResults.json`.
- Clean runtime console after final cleanup: **0 errors, 0 warnings**. Editor settings and background-running setting used during verification were restored afterward.
- Shader compilation on DX12: **0 errors**. Unity reports three conservative “potentially uninitialized” compiler warnings associated with the Map, ProjectedNormal and Region functions. Rendered and GPU regression checks pass. These shader compiler warnings are distinct from the clean runtime console.

Screenshots are in this folder, including `DemoUI.png`, `Families.png`, `Recipes.png`, `Cutaway.png`, `Legend.png` and `Closeup-*.png`.

Not executed: standalone Player build, mobile-device/API validation, other graphics APIs, GPU profiling on deployment hardware, lightmap baking. The implementation does not claim volume continuity, arbitrary slice caps, displacement, refraction or automatic multi-layer baking. Those boundaries are documented in the README. Original geological source verification remains deliberately unresolved where the supplied brief requires it.
