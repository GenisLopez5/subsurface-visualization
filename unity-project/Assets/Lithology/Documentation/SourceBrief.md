# Implement a reusable lithology material system in Unity

You are working as the implementation agent in my Unity workspace. Build the system described below. This prompt is self-contained; do not assume earlier chats, external JSON files, source PDFs, or existing texture packs are available.

Create the C# scripts, compatible shader assets, complete seeded catalogue, material authoring tools, replaceable procedural preview textures, demonstration scene, and documentation. Work through implementation and verification; a plan or scaffold alone is not the requested deliverable. Follow the repository's instructions and existing conventions. Make reasonable reversible implementation choices without stopping for routine questions.

## Goal and scope

Represent lithologies on 3D geological layers, borehole intervals, irregular meshes, and exposed cut faces using shared assets:

**Lithology identity + base texture family + component layers + structural patterns + visual parameters.**

Keep 180 source labels distinct while sharing 25 base texture families, reusable masks, and shader operations. Support an opaque textured view and a readable legend/pattern view. Make the system usable immediately with clearly identified illustrative procedural assets and easy to improve by assigning real PBR scans later.

The source labels come from a pasted AI extraction of PDF legends. The PDFs and codebooks are unavailable. Preserve ambiguities and source spelling. This is an illustrative rendering system, not a reconstruction of real specimens or a numerical geological simulator. Its uncertain entries are valid data states that must render clearly.

## Inspect the project first

1. Read applicable repository instructions. Identify the Unity project, Unity version, installed packages, assembly definitions, rendering conventions, tests, and relevant existing geology code.
2. Inspect the active render pipeline, including Graphics and Quality settings overrides. Implement for that project's actual Built-in, URP, or HDRP setup. Preserve the Unity version, pipeline, unrelated assets, and existing settings.
3. Reuse installed APIs and dependencies. Check official documentation and installed package examples for that version instead of inventing APIs or Shader Graph serialization.
4. If no Unity project exists, produce a portable `Assets/Lithology` implementation with a clearly documented Unity 6 URP target, an Editor bootstrap entry point, and import instructions. Do not invent a tested Unity environment or hand-fabricate an entire project configuration. If Unity cannot run here, complete the files and report exactly which import/compile/visual checks remain unexecuted.
5. Keep runtime code free of `UnityEditor` dependencies. Put Editor tooling and tests in appropriate folders/assemblies. Keep package-specific code isolated so unavailable packages do not create compiler errors.

## Data model and C# deliverables

Use these responsibilities; adapt names to existing conventions without dropping behavior:

| Type or service | Required responsibility |
|---|---|
| `BaseTextureSet` | ScriptableObject for B01-B25: stable ID, description, macro/base PBR maps, optional independently tiled grain/detail maps or procedural grain channels, optional AO/height/component masks, separate scales, capability flags, preview/real-asset status, source/license notes. B25 is conditional. |
| `LithologyDefinition` | Stable ID, exact raw label, provisional normalized-name candidate, render role, interpretation status, source verification flag, provenance, and reference to a recipe. Preserve geological identity separately from shared rendering. |
| `LithologyMaterialRecipe` | Base reference or explicit null, component layers, semantic modifiers, structural settings, palette, grain scale, normal/smoothness settings, seeds, and coordinate settings. Record that chosen numerical defaults are illustrative. |
| `ModifierDefinition` / `ModifierSettings` | The complete 53 semantic IDs below, their applicability, implemented operation, optional conditions, and parameters. Multiple IDs can share an operation. |
| `LithologyCatalog` and resolver | Serialized catalogue and fast runtime lookups. Return the definition plus resolution status/diagnostics; preserve unrecognized input instead of silently guessing a rock. |
| `LithologyRecipeCompiler` | Convert semantic modifiers into the supported masks, component layers, structural controls, and shader parameters; define deterministic composition/precedence and diagnose unsupported combinations. |
| `LithologyMaterialCache` | Create/reuse materials for effective recipes, view modes, and overrides; manage ownership and cleanup. Avoid per-frame or per-voxel material creation. |
| `LithologyRenderer` | Apply a selected label/definition to a Renderer and selected material slot, preserving other slots. Apply on relevant changes rather than allocating every frame. Support explicit host + modifiers when a host is supplied. |
| Editor builder and authoring tools | Seed/update the catalogue, generate preview assets/materials, bind real textures, validate mappings, apply recipes to selected renderers, and create the demonstration scene. |

Implement a straightforward public API for resolving a raw label, applying a definition to a Renderer, switching display mode, and supplying visual overrides. Include a small compiling integration example in documentation. Use explicit references or the project's loading system; do not require scene-object searches to find the catalogue on every call.

### Identity, serialization, and resolution

- Create a checked-in seed dataset from the complete mapping in this prompt. The implementation must not depend on downloading an attachment or accessing ChatGPT.
- Use explicit stable IDs. Do not use unstable runtime string hashes as persistent IDs or procedural seeds.
- Keep raw labels exactly as supplied. Case/whitespace normalization may assist lookup, but preserve punctuation, hyphens, compound labels, and the original string. Detect collisions and report ambiguity.
- Record the corresponding row in this prompt/pasted extraction as the available source. Preserve the occurrence counts and duplicate-label locations supplied below; keep unavailable original extraction categories, PDF filenames/pages, sample observations, and codebook details unknown rather than fabricating provenance.
- Provisional normalized names are suggestions, not automatically approved aliases. Add explicit verified alias support without merging original records.
- Store nullable host/base choices deliberately. Distinguish physical rock/sediment, procedural material, component, descriptor, context-dependent material, generic class, and data/sample status. Dispatch null-base records by role rather than treating every null as a broken material.
- Keep selected bases, optional component texture sources, and possible-but-unselected bases separate. Never automatically promote a candidate to a confirmed selection.
- Store numeric visual defaults separately from measured geology. Mask coverage, tint, gloss, grain exaggeration, and chosen scales are not mineral percentages or observed specimen properties.
- Choose useful numerical display defaults so the system is runnable. Uncertainty forbids treating those defaults as measurements; it does not require leaving every control unset. Preserve user-confirmed interpretation overrides separately from generated defaults.
- Choose a Unity-compatible serialization strategy. For `JsonUtility`, use serializable DTO fields and supported arrays/lists; construct ScriptableObjects through Unity's APIs and rebuild runtime dictionaries. Reuse an installed JSON library if appropriate. Do not assume Unity serializes arbitrary dictionaries or properties.
- Reject malformed entries or report them with a safe display fallback. Do not silently ignore unknown modifier IDs, lose source records, or turn parse failures into apparently valid lithologies.

## Shader and rendering implementation

Implement a genuinely compiling Lit shader for the detected pipeline, plus any small helper shaders/subgraphs needed. Use valid installed-version Shader Graph assets when they can be authored reliably; otherwise implement compatible ShaderLab/HLSL. Do not deliver guessed `.shadergraph` JSON or instructions telling me to manually build the entire graph.

Implement the shadow/depth behavior required by the project's lighting. Every exposed control must drive a real shader or material-authoring operation. A zero-strength component must restore the host's color, normals, and parameter maps. Metadata-only or unresolved operations must identify their status clearly rather than pretend to change geology.

Support a shared base PBR set, one or two active component layers, and reusable detail/structural controls. Compile semantic recipes into a manageable set of operations rather than evaluating all 53 modifiers or sampling all 25 bases per fragment. Preserve more complex recipe data and choose an explicit baking/authoring route if a recipe exceeds runtime layer limits; never silently discard modifiers.

### Surface controls

- Base color/palette, independently colored mineral regions, normal strength, smoothness, and optional AO.
- Separate grain/crystal scale, bedding spacing, cleavage spacing, fracture scale, clast scale, vein width, and broad mottling scale. Scaling grain detail must not resize large fractures and bedding.
- Host/component blending using appropriate grain, matrix, pore-fill, patch, vein, or lamination masks. Use consistent masks for color, surface finish, and relief.
- Rounded versus angular clasts, generic carbonate grains, shell fragments, ooids, flakes, glass/crystal fragments, vesicles, and welded pyroclastic detail.
- Opaque legend/pattern mode with stable colors and readable identifiers. Choose an illustrative palette because no source-legend colors were supplied. Keep missing-data states visible without creating physical holes.
- A controllable distinction between physical texture scale and schematic exaggeration. Record the assumed Unity-unit conversion. Use mipmaps/filtering and an appropriate detail fade so subpixel grains do not shimmer in the geological overview.

Author generated previews with separate grain/detail and macro/structural controls. If a supplied scan bakes grains and large cracks into one inseparable image, keep its base scale stable and change separate detail patterns or use an appropriate alternate asset. Expose the asset's capabilities and disable unsupported independent rescaling controls; do not claim that rescaling the entire scan independently changes only its grain size.

Conceptual color/smoothness blending:

```hlsl
float m = saturate(componentMask);
float3 color = lerp(hostColor, componentColor, m);
float smoothness = lerp(hostSmoothness, componentSmoothness, m);
```

For additive normal detail, use a normal-aware operation such as reoriented normal mapping. For transitions between different material regions, decode normals into one coordinate space, blend, and normalize. Do not blend encoded normal RGB as ordinary colors. Verify projection signs, normal spaces, and negative/nonuniform scales. Support triplanar meshes without relying on authored UVs; handle tangent requirements explicitly.

### Coordinates and cut surfaces

- Implement UV and triplanar projection where practical, with triplanar as the default for irregular geological meshes.
- Offer world/geological coordinates for alignment across static adjacent units and object/rest coordinates for movable specimens or exploded layers. Do not let textures swim unintentionally as objects move.
- Give bedding a dedicated frame and give slaty cleavage its own independent direction. A useful planar bedding coordinate is `dot(geologicalPosition - geologicalOrigin, beddingNormal) / layerSpacing`; normalize directions, validate positive spacing, and add controlled irregularity.
- Keep per-object coordinates and overrides from unexpectedly changing other renderers sharing a material. If geological frames need material instances, cache them per stable frame/recipe and update state without unbounded allocations.
- Ensure exposed faces receive the intended material. The demo can use an actual static cutaway/stacked mesh; a clipping shader alone does not create a cap mesh.
- Document the boundary: triplanar 2D maps decorate surfaces. They do not establish coherent individual clasts inside a volume. True arbitrary-slice continuity requires 3D fields or geometry and is an extension, not a claimed feature of this initial system.

### PBR/import conventions

- Default bulk rock to opaque, nonmetallic behavior. Use local metallic-looking pyrite components; quartz, calcite, and mica can reflect light while remaining dielectric.
- Unity normal maps use Y+/OpenGL convention. Import normal maps correctly and provide a documented green-channel conversion option for a supplied opposite-convention map.
- Treat base-color textures as color data and roughness, smoothness, masks, and AO as linear data. For conventional roughness, `smoothness = 1 - roughness`.
- If targeting standard URP Lit packing: R=Metallic, G=Occlusion, B=unused, A=Smoothness. If targeting HDRP Mask Map: R=Metallic, G=AO, B=Detail Mask, A=Smoothness. Disable sRGB for these data textures. A custom shader may use another explicitly documented layout.
- Normal maps do not change silhouettes. Height needs an actual displacement implementation to modify geometry; holes and detached fragments may require meshes. Avoid implying supported displacement if only normal shading is implemented.
- Keep transparent/refractive crystal rendering optional; the initial opaque system must work without it.

### Runtime resource and performance rules

Reuse materials and texture assets. Avoid repeated `Renderer.material` access that creates unintended instances. Do not modify source material assets or another object's overrides while applying a recipe. Release owned runtime materials appropriately and keep caches bounded when values change.

Include every appearance-relevant setting in cache identity or explicit invalidation, including recipe revisions, assigned texture sets, view mode, seeds, and overrides. Reset owned state correctly across play-mode transitions, including disabled domain reload when relevant. Avoid texture generation, catalogue parsing, and asset writes in per-frame callbacks or unrestricted `OnValidate`.

Each triplanar texture requires three directional samples. Keep active layer counts modest and use packed data maps where appropriate. Maintain SRP Batcher compatibility where practical; `MaterialPropertyBlock` is not a universal optimization and is incompatible with the SRP Batcher. Profile before selecting that route. Do not claim SRP batching or draw-call reduction merely because objects share a shader.

## Editor workflow and generated assets

Create clear menu commands under `Tools/Lithology` for: initialize/update catalogue, generate preview assets, validate, and create/open the demo. A single bootstrap command should produce a usable first version.

Initialization must be repeatable: preserve stable IDs, existing asset references/GUIDs, and user-assigned real textures or overrides. Update generated records in place; keep generated defaults separate from authored overrides. Limit regeneration to owned output folders and use Undo where editing existing scene objects/assets is appropriate.

Add a practical catalogue inspector/window with label search, base-family filtering, source/interpretation status, material preview, and a texture assignment workflow. Make it easy to replace a procedural set with a real PBR set without editing C# or the shader. Provide a texture procurement manifest with B IDs, search phrases, required maps, scale, assignments, and provenance/license fields.

Generate deterministic, tileable, explicitly illustrative preview PBR textures/masks for the common families at a modest configurable resolution. Use fixed documented seeds and structurally different patterns: fine matrix, layers, sand grains, rounded/ angular clasts, carbonate grains/shells, crystalline mineral regions, glass, and vesicles. Do not make every family a recolored copy of one noise texture. Use safe neutral maps if an optional input is missing.

Use a local deterministic random generator without altering global application random state. Generate normal/height/mask detail from consistent patterns and verify that raised features appear raised. Ensure wrap continuity at texture boundaries. Creating the demo must preserve unsaved scene work.

Register B25 and the reference-dependent bound-carbonate detail as conditional slots. A diagnostic preview is acceptable, but never activate a purported serpentinite or coral interpretation automatically. No external scan pack is required to run the system; actual photorealistic scans remain replaceable assets. Prefer fresh/core/cut-face references when scans are supplied, and keep weathering/debris as a separate treatment.

## Base texture family catalogue

Create these 25 stable family slots. The full source-label mapping follows. Each set should accept albedo, normal, roughness/smoothness, optional AO/height, and optional component masks. Search phrases are procurement guidance, not claims that a particular downloadable asset exists.

| ID | Family | Asset requirement / search guidance |
|---|---|---|
| B01 | Fine clay / mud matrix | A fine, non-granular earth or mudstone surface without conspicuous sand grains or mandatory lamination. Prefer a neutral, fresh or cut surface; use separate consolidation and fracture settings for clay versus claystone. Search: clay mudstone seamless PBR; fresh claystone texture. |
| B02 | Laminated shale | A fine-grained shale surface with a readable bedding or fissility direction. Obtain restrained relief and a directional mask so lamination can follow the geological bedding orientation. Search: laminated shale seamless PBR; shale fresh cut surface. |
| B03 | Silt / siltstone | A subtly granular fine siltstone surface, finer than the sandstone set. Avoid assigning resolvable coarse sand grains simply to make the material visible at a distance. Search: siltstone seamless PBR; fine siltstone texture. |
| B04 | Loose sand | A neutral sand surface with individually separable grains and no dunes, footprints, shells, or cemented bedding baked into the image. Keep grain-scale detail separable from broad color variation. Search: neutral sand grains seamless PBR; sand closeup texture. |
| B05 | Cemented sandstone | A sandstone surface showing packed grains and an intergranular matrix or cement. Prefer an unweathered surface; avoid masonry blocks and strong bedding that cannot be disabled. Search: sandstone fresh surface seamless PBR; sandstone grain matrix texture. |
| B06 | Rounded clasts / conglomerate | A rounded to subrounded pebble-in-matrix texture with a separate clast mask. Clast scale, abundance, sorting, and matrix texture must be adjustable independently. Search: conglomerate rock seamless PBR; rounded pebble matrix texture. |
| B07 | Angular clasts / breccia | An angular rock-fragment-in-matrix texture with an independent clast mask and restrained weathering. It must retain angular outlines instead of recoloring the rounded-clast set. Search: breccia rock seamless PBR; angular rock fragments matrix texture. |
| B08 | Fine micritic limestone | A fine, relatively uniform carbonate matrix without compulsory fossils, ooids, or large crystals. This supplies the matrix for limestone, argillaceous limestone, and marl recipes. Search: micritic limestone seamless PBR; fine limestone fresh surface. |
| B09 | Crystalline carbonate / dolostone | A restrained interlocking carbonate-crystal surface suitable as a visual starting point for crystalline dolostone. Fine crystal detail should be independent of any large fractures or veins. Search: crystalline dolostone seamless PBR; dolomite rock fresh surface texture. |
| B10 | Chalk | A pale, very fine and matte chalk surface, with subtle porous relief and no mandatory flint nodules or weathered cliff staining. Search: chalk rock seamless PBR; fresh chalk surface texture. |
| B11 | Shell-rich limestone / coquina | Recognizable shell fragments in carbonate material, with shell and void masks. Prefer assets that allow shell size and abundance to vary without changing the whole matrix. Search: coquina seamless PBR; shell limestone texture mask. |
| B12 | Granular / oolitic limestone | A carbonate-grain texture with a separate grain-versus-matrix mask. Keep an ooid-specific variant or mask; generic packstone and grainstone must not automatically inherit ooids. Search: granular limestone seamless PBR; oolitic limestone ooid texture. |
| B13 | Coal / lignite | A carbon-rich rock texture with distinguishable dull and shiny patches, subtle cleat or fracture detail, and a configurable dark palette. A lignite reference can add brown or woody details when warranted. Search: coal seamless PBR; lignite fresh surface texture. |
| B14 | Gypsum | A massive gypsum reference with fine crystalline detail. Fibrous satin spar and transparent crystal specimens are optional variants, not mandatory properties of every gypsum interval. Search: massive gypsum seamless PBR; gypsum rock fresh texture. |
| B15 | Anhydrite | A compact massive anhydrite reference with restrained crystalline relief, separate from the gypsum surface. Do not rely on color alone to establish mineral identity. Search: massive anhydrite rock texture; anhydrite fresh core surface. |
| B16 | Halite / salt | A rock-salt crystalline surface with adjustable crystal scale. Cube-like crystal cues may be a detail mask, but the bulk interval need not be transparent. This set can be reused visually for sylvite without merging their identities. Search: rock salt halite seamless PBR; halite crystalline texture. |
| B17 | Chert | A very fine, dense silica-rich surface with restrained conchoidal-fracture cues. Color, banding, porcelain-like appearance, and sheen should be independent modifiers. Search: chert fresh surface seamless texture; flint conchoidal surface PBR. |
| B18 | Tuff | An ash-rich pyroclastic matrix with independently maskable lithic, crystal, and glass fragments. Avoid baking a specific welding direction or large lapilli into the only base asset. Search: tuff rock seamless PBR; volcanic ash tuff texture. |
| B19 | Volcanic cinder / scoria | A vesicular volcanic material with a usable cavity mask and normal or height map. Visible holes at close range may require geometry instead of surface shading alone. Search: volcanic scoria seamless PBR; cinder vesicular rock texture. |
| B20 | Fine mafic / intermediate volcanic rock | A fine volcanic groundmass suitable for basalt and andesite presets. Use a non-vesicular, non-porphyritic reference as the neutral starting point; add holes or phenocrysts only when justified. Search: basalt fine groundmass seamless PBR; andesite fresh surface texture. |
| B21 | Fine felsic / intermediate volcanic rock | A fine volcanic groundmass for the rhyolite, dacite, and quartz-latite visual presets. Flow banding and conspicuous phenocrysts should be independently selectable rather than compulsory. This shared rendering family is not a claim that all three rocks have identical composition. Search: rhyolite fine groundmass seamless PBR; dacite fresh surface texture. |
| B22 | Coarse felsic to intermediate intrusive rock | An interlocking crystal texture with separately adjustable pale feldspar, quartz-like, and dark mineral regions. Use for granite, granodiorite, and quartz monzonite visual presets while retaining separate geological labels. Search: granite interlocking crystals seamless PBR; granodiorite fresh surface texture. |
| B23 | Coarse intermediate / mafic intrusive rock | An interlocking crystal texture with adjustable light-versus-dark mineral regions, suitable for diorite and gabbro visual presets. Do not substitute a rounded sand-grain texture. Search: diorite seamless PBR; gabbro interlocking crystals texture. |
| B24 | Obsidian | A dense volcanic glass surface with conchoidal-fracture detail and configurable smoothness. Keep it dielectric; visible glassy reflection does not make it a metal. Search: obsidian volcanic glass seamless PBR; obsidian fracture texture. |
| B25 | Serpentine-rich rock (conditional) | A verified serpentine-rich rock reference with optional mottling and vein masks. Acquire or assign this only if the source label Serpentine denotes a rock; a mineral occurrence does not specify a whole-rock texture. Search: serpentinite fresh surface PBR; serpentine rich rock texture. |


## Reusable detail assets

Implement these 11 detail-asset groups; a group may contain several distinct masks. Reuse them across recipes. Keep shell and ooid masks separate, and keep a bound-carbonate pattern conditional on a reference.

| ID | Group | Purpose |
|---|---|---|
| D01 | Multiscale noise and component coverage masks | Spatially varying color and mixture coverage; distinguish fine mixtures from patches. |
| D02 | Directional bands and fine lamination | Shaly, laminated, striped, and other verified directional fabrics. |
| D03 | Independent grain and mineral region masks | Change grain scale and color balance without rescaling every fracture. |
| D04 | Rounded clast masks | Conglomeratic modifiers and pebble-in-matrix fabrics. |
| D05 | Angular clast masks | Breccia and verified angular-fragment components. |
| D06 | Shell fragments and ooids as separate masks | Keep bioclastic and oolitic identity distinct. |
| D07 | Crystal, glass-shard, and mica-flake masks | Mineral and tuff components, each with an appropriate shape. |
| D08 | Vein and replacement masks | Reference-based cement, mineralization, and alteration occurrence patterns. |
| D09 | Pores, vesicles, and fracture detail | Local surface relief and component placement; supplement with geometry when visible at silhouette. |
| D10 | Flattened pyroclastic lenses / welding pattern | A distinct, reference-based directional detail for welded tuff; can be authored by deforming appropriate pyroclastic patterns. |
| D11 | Bound carbonate fabric (conditional) | Binding, baffling, or framework pattern identified from a reference. Acquire only when its form is known; no universal coral pattern is assumed. |

## Complete source-label mapping

Implement exactly one source-label record for each row below: 180 unique labels. Retain raw spelling, case, punctuation, and hyphens. Different labels may share a recipe but must retain separate selectable identities. Do not require any external JSON or earlier conversation to construct this catalogue.

Notation: `—` means no base texture family is assigned. Procedural rows create their specified appearance; hostless components and unresolved rows use a labeled schematic fallback until context is supplied. `V` means verify the original legend/context; an assigned base is only a provisional illustrative default. A `name candidate` is not an approved alias. `?` marks an optional modifier enabled only when supported by the reference. `CODE` rows must have null base, no decoded suffix, and requiresSourceLegend=true. Component sources are blend resources, not extra hosts or measured ratios. All numerical shader settings must be explicitly artistic defaults unless observed data supplies them.

### B01–B03: fine sediment and shale

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Clay` | B01 | consolidation=unconsolidated |
| `Claystone` | B01 | consolidation=consolidated |
| `Plastic Clay` | B01 | consolidation=unconsolidated, plasticity; plasticity is metadata only |
| `Silty Clay` | B01 | consolidation=unconsolidated, silt_content |
| `Mudstone` | B01 | consolidation=consolidated |
| `Clay-Mudstone` | B01 | clay_content, consolidation=consolidated |
| `Till` | B01 | poor_sorting, rounded_clasts, angular_clasts; component source B06,B07 |
| `Bentonite` | B01 | base preset |
| `Marl` | B01 | clay_content, calcareous_component; component source B08 |
| `Marlstone` | B01 | clay_content, calcareous_component, consolidation=consolidated; component source B08 |
| `Marl - Dolo` | B01 | clay_content, dolomite_component; component source B09; V: name candidate Dolomitic Marl |
| `Marl - Calc` | B01 | clay_content, calcareous_component; component source B08; V: name candidate Calcitic or Calcareous Marl |
| `Clyst-Tuffaceous` | B01 | consolidation=consolidated, volcanic_component; component source B18; V: name candidate Tuffaceous Claystone |
| `Clay-Shale` | B02 | clay_content, lamination |
| `Silty Clay-Shale` | B02 | clay_content, silt_content, lamination |
| `Sandy Clay-Shale` | B02 | clay_content, sand_content, lamination |
| `Calcareous Clay-Shale` | B02 | clay_content, calcareous_component, lamination |
| `Calc Clay-Shale` | B02 | clay_content, calcareous_component, lamination; V: name candidate Calcareous Clay-Shale |
| `Shale` | B02 | lamination |
| `Slaty Shale` | B02 | lamination, cleavage; cleavage direction independent of bedding |
| `Shale Gray` | B02 | lamination, tint=gray |
| `Shale Colored` | B02 | lamination, tint=unspecified; V: palette unspecified |
| `Shale Brown` | B02 | lamination, tint=brown |
| `Shale Green` | B02 | lamination, tint=green |
| `Silty Shale` | B02 | lamination, silt_content |
| `Calcareous Clay-Shal` | B02 | clay_content, lamination, calcareous_component; V: name candidate Calcareous Clay-Shale |
| `Carbonaceous Sh` | B02 | organic_content, lamination; V: name candidate Carbonaceous Shale |
| `Silt` | B03 | consolidation=unconsolidated |
| `Siltstone` | B03 | consolidation=consolidated |
| `Silty Siltstone` | B03 | consolidation=consolidated; preserve redundant wording |
| `Shaly Siltstone` | B03 | clay_content, lamination?; component source B02; lamination optional, never inferred solely from shaly |
| `Siltst-Tuffaceous` | B03 | volcanic_component; component source B18; V: name candidate Tuffaceous Siltstone |

### B04–B07: sand and coarse clastics

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Sand` | B04 | consolidation=unconsolidated |
| `Fine Sand` | B04 | grain_size=fine, consolidation=unconsolidated |
| `Coarse Sand` | B04 | grain_size=coarse, consolidation=unconsolidated |
| `Medium Sand` | B04 | grain_size=medium, consolidation=unconsolidated |
| `Salt-Pepper Sand` | B04 | tint=light/dark grains |
| `Clay Choke Sand` | B04 | clay_content; component source B01; V: provisional clay-choked interpretation; pore-fill/coating pattern; V: name candidate Sand with clay-rich pore/matrix material |
| `Congl. Sand` | B04 | rounded_clasts; component source B06; V: name candidate Conglomeratic Sand |
| `Sandstone` | B05 | consolidation=consolidated |
| `Fine Sandstone` | B05 | grain_size=fine, consolidation=consolidated |
| `Medium Sandstone` | B05 | grain_size=medium, consolidation=consolidated |
| `Coarse Sandstone` | B05 | grain_size=coarse, consolidation=consolidated |
| `Calcareous Sandstone` | B05 | calcareous_component |
| `Dolomitic Sandstone` | B05 | dolomite_component |
| `Shaly Sandstone` | B05 | clay_content, lamination?; component source B02; lamination optional, never inferred solely from shaly |
| `Sandstone-Tuffaceous` | B05 | volcanic_component; component source B18 |
| `Congl. Sandstone` | B05 | rounded_clasts; component source B06; V: name candidate Conglomeratic Sandstone |
| `Granite Wash` | B05 | granitic_clasts; V: sedimentary fabric; consolidation/size unknown; candidates B04/B06/B07 |
| `Conglomerate` | B06 | rounded_clasts |
| `Pebbles` | B06 | grain_size=pebble-sized; V: size only; B06 is illustrative; shape/matrix/consolidation unknown; candidate B07 |
| `Breccia` | B07 | angular_clasts |

### B08–B12: carbonates

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Argillaceous Limesto` | B08 | clay_content; component source B01; V: name candidate Argillaceous Limestone |
| `Limestone` | B08 | base preset |
| `Argillaceous Limestone` | B08 | clay_content; component source B01 |
| `Dolomitic Limestone` | B08 | dolomite_component; component source B09 |
| `Bituminous Limestone` | B08 | organic_content |
| `Wackstone` | B08 | carbonate_grains, mud_supported; component source B12; V: name candidate Wackestone |
| `Dolomite` | B09 | V: provisional rock interpretation; mineral meaning also possible |
| `Fine Dolomite` | B09 | grain_size=fine; V: confirm source interpretation |
| `Sandy Dolomite` | B09 | sand_content |
| `Sandy and Calcareous Dolomite` | B09 | sand_content, calcareous_component |
| `Argil and Sandy Dolomite` | B09 | clay_content, sand_content; V: name candidate Argillaceous and Sandy Dolomite |
| `Calc. Dolomite` | B09 | calcareous_component; V: name candidate Calcitic or Calcareous Dolomite |
| `Dolostone` | B09 | base preset |
| `Chalk` | B10 | base preset |
| `Coquina` | B11 | shell_fragments |
| `Oolitic/Oolic Limestone` | B12 | ooids; V: name candidate Oolitic Limestone |
| `Packstone` | B12 | carbonate_grains, grain_supported=mud present; component source B08; carbonate mud remains between touching grains |
| `Grainstone` | B12 | carbonate_grains, grain_supported=no carbonate mud; no depositional carbonate mud; later cement possible; no assumed open porosity |

### B13–B17: coal, evaporites, and chert

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Coal` | B13 | base preset |
| `Coal-Lignite` | B13 | coal_lignite_variant |
| `Gypsum` | B14 | base preset |
| `Anhydrite` | B15 | base preset |
| `Halite` | B16 | base preset |
| `Salt` | B16 | V: unspecified salt chemistry; generic appearance |
| `Sylvite` | B16 | share crystal template only; no mandatory red color/transparency |
| `Chert` | B17 | base preset |
| `Chert - Argill` | B17 | clay_content; component source B01; V: name candidate Argillaceous Chert |
| `Chert - Glassy` | B17 | surface_sheen=glassy |
| `Chert - Porcel` | B17 | surface_sheen=porcelain-like; V: name candidate Porcellanous or Porcelain-like Chert |
| `Chert - Tiger Stripe` | B17 | banding |
| `Chert - Undiff` | B17 | V: name candidate Undifferentiated Chert |

### B18–B24: volcanic and crystalline rocks

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Tuff` | B18 | base preset |
| `Lithic Tuff` | B18 | lithic_fragments |
| `Welded Tuff` | B18 | welding; distinct flattened/directional pyroclastic pattern |
| `Crystalline Tuff` | B18 | crystal_fragments; V: crystal-rich interpretation provisional; V: name candidate Crystal-rich Tuff |
| `Glassy Tuff` | B18 | glass_shards; retain fragmental tuff, not massive glass |
| `Volcanic Cinder` | B19 | vesicles |
| `Andesite` | B20 | igneous_mineral_balance |
| `Basalt` | B20 | igneous_mineral_balance |
| `Dacite` | B21 | igneous_mineral_balance |
| `Rhyolite` | B21 | igneous_mineral_balance |
| `Quartz Latite` | B21 | igneous_mineral_balance |
| `Granite` | B22 | igneous_mineral_balance |
| `Granodiorite` | B22 | igneous_mineral_balance |
| `Quartz Monzonite` | B22 | igneous_mineral_balance |
| `Diorite` | B23 | igneous_mineral_balance |
| `Gabbro` | B23 | igneous_mineral_balance |
| `Quartz Diorite` | B23 | igneous_mineral_balance |
| `Hornbl-Qtz-Dio` | B23 | igneous_mineral_balance; V: name candidate Hornblende Quartz Diorite |
| `Obsidian` | B24 | surface_sheen |

### Procedural materials

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Tar` | — | organic_content, surface_sheen; procedural dark dielectric, adjustable smoothness/noise |
| `Bitumen` | — | organic_content, surface_sheen; procedural dark dielectric, adjustable smoothness/noise |

### Mineral/components: host unspecified

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Pyrite` | — | pyrite_component |
| `Glauconite` | — | glauconite_component |
| `Kaolinite` | — | kaolinite_component |
| `Siderite or Limonite` | — | iron_mineral_component; V: keep both alternatives; generic mineral marker |
| `Phosphate` | — | phosphate_component; V: mineral/fabric unspecified; generic component marker |
| `Quartz` | — | silica_component |
| `Calcite` | — | calcite_component |
| `Recrystallized Calcite` | — | calcite_component, recrystallization |
| `Mica` | — | mica_flakes |
| `Muscovite` | — | muscovite_component |
| `Shell Fragments` | — | shell_fragments |

### Modifiers: require an independently identified host

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Conglomeratic` | — | rounded_clasts |
| `Dolomitic` | — | dolomite_component |
| `Marly` | — | clay_content, carbonate_component |
| `Kaolinitic` | — | kaolinite_component |
| `Siliceous` | — | silica_component |
| `Calcareous` | — | calcareous_component |
| `Argillaceous` | — | clay_content |
| `Carbonaceous` | — | organic_content |
| `Sandy` | — | sand_content |
| `Silty` | — | silt_content |
| `Altered Zone` | — | alteration_unspecified; V: confirm source interpretation |
| `Botitization (Biotitization)` | — | biotitization; V: name candidate Biotitization |
| `Chloritization` | — | chloritization |
| `Sericitization` | — | sericitization |
| `Silicification` | — | silicification |
| `Carbonaceous Mat` | — | organic_content; V: name candidate Carbonaceous Material |

### Context-dependent materials

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Paleosol` | — | paleosol_features; V: identify parent first; candidates B01/B03/B05 |
| `Boundstone` | — | bound_framework; V: reference-specific bound fabric; no automatic coral/framework; keep generic fallback |
| `Serpentine` | — | serpentine_component; V: mineral versus rock unresolved; B25 only after rock interpretation is confirmed |
| `Carbonates` | — | carbonate_component; V: rock group versus component unresolved; select no mineral or whole-rock texture yet |
| `Cement` | — | cement_unspecified; V: natural versus drilling material unresolved |

### Broad or unresolved geological classes

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Evaporites` | — | V: generic class; candidates B14/B15/B16 |
| `Salt (K-Na-Mg)` | — | V: generic salt class; composition/ratios unresolved; candidate B16 |
| `Gypsum/Anhyd.` | — | V: slash meaning/ratio unresolved; candidates B14/B15; V: name candidate Gypsum / Anhydrite |
| `Volcanics` | — | V: generic volcanic class; no assumed fabric/composition |
| `Volcanic Rock` | — | V: generic volcanic class; no assumed fabric/composition |
| `Pyroclastics` | — | V: generic class; grain size/clasts/welding unknown |
| `Volcaniclastics Seds` | — | volcanic_component; V: sedimentary host/fabric unknown; volcanic component retained; V: name candidate Volcaniclastic Sediments |
| `Felsic Silicic Dike` | — | dike_geometry; V: preserve felsic composition/body shape; grain fabric unspecified |
| `Igneous (Acidic)` | — | V: broad felsic class; fine/coarse/glassy fabric unspecified |
| `Igneous (Basic)` | — | V: broad mafic class; fine/coarse fabric unspecified |
| `Intrusives` | — | V: generic intrusive class; mineralogy/crystal scale unknown |
| `Extrusive Rocks` | — | V: generic extrusive class; composition/fabric unknown |
| `Metamorphic` | — | V: generic class; do not assume foliation |
| `Metamorphics` | — | V: generic class; do not assume foliation |

### Data and sample statuses

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Cement Contam.` | — | contamination_status; sample-quality overlay; retain known host separately; V: name candidate Cement Contamination |
| `Unknown` | — | distinct unknown-lithology display state |
| `Not Available` | — | distinct unavailable-data display state |
| `No Sample` | — | distinct unsampled display state; preserve interval geometry |
| `Others` | — | distinct unspecified-category display state |

### Opaque source-specific codes

| Raw source label | Base | Modifiers / instructions |
|---|---|---|
| `Clay-S` | — | CODE: clay-family hint only; suffix undecoded |
| `Clay-SCS` | — | CODE: clay-family hint only; suffix undecoded |
| `Clay-M` | — | CODE: clay-family hint only; suffix undecoded |
| `Clay-CCS` | — | CODE: clay-family hint only; suffix undecoded |
| `Clay-SACS` | — | CODE: clay-family hint only; suffix undecoded |
| `Sand-SIS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-SS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-FS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-MS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-CS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-FSS` | — | CODE: sand-family hint only; suffix undecoded |
| `Sand-MSS` | — | CODE: sand-family hint only; suffix undecoded |
| `Limestone-AL` | — | CODE: limestone-family hint only; suffix undecoded |
| `Limestone-SL` | — | CODE: limestone-family hint only; suffix undecoded |
| `Limestone-L` | — | CODE: limestone-family hint only; suffix undecoded |
| `Dolomites-AD` | — | CODE: dolomite-family hint only; suffix undecoded |
| `Dolomites-CD` | — | CODE: dolomite-family hint only; suffix undecoded |
| `Dolomites-D` | — | CODE: dolomite-family hint only; suffix undecoded |
| `Others-H` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-A` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-GY` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-E` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-CL` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-S` | — | CODE: other/unspecified hint only; suffix undecoded |
| `Others-V` | — | CODE: other/unspecified hint only; suffix undecoded |

### Source preservation checks

The original extraction has 182 occurrences and 180 unique labels. Preserve two source locations for each duplicated label: `Argillaceous Limesto` appeared under section 1/Shale and section 2/Limestone; `Dolomitic` appeared under section 2/Dolomite and section 5/Textural/Compositional. Repeat the Chert prefix for the supplied shorthand Glassy, Porcel, Tiger Stripe, and Undiff, while recording that prefix expansion. Preserve compound labels as one record. Operational/fluid indicators explicitly excluded from the extraction are outside this catalogue.

## Complete modifier instruction vocabulary

Implement the following 53 semantic IDs. Reuse shader operations and masks across IDs; do not create 53 unrelated shaders. Descriptors and components remain metadata without a known host. Metadata/status/geometry-only operations must not fabricate a rock texture. Values, coverage, color, and pattern scale are artistic defaults rather than measured fractions.

| Modifier ID | Operation / constraint |
|---|---|
| `tint` | Remap palette or mineral-region colors; exact RGB remains an artistic setting. |
| `grain_size` | Scale grains/crystals independently of beds, fractures, veins, and display exaggeration. |
| `consolidation` | Select loose/consolidated appearance through matrix/contact/fracture cues; no strength simulation. |
| `clay_content` | Add fine clay matrix or pore-filling component using an appropriate mask. |
| `silt_content` | Add subtle fine grains; avoid giant visible silt at physical scale. |
| `sand_content` | Add sand grains to the host; size, composition, and coverage remain independent. |
| `calcareous_component` | Calcium-carbonate component; mineral, occurrence, brightness, and amount are unspecified. |
| `calcite_component` | Explicit calcite component; choose grain/cement/vein occurrence separately. |
| `dolomite_component` | Dolomitic matrix/crystal component; it may have little visible effect. |
| `silica_component` | Silica grains, cement, or veins according to occurrence; no automatic transparency. |
| `organic_content` | Dark organic particles/patches/laminae; no automatic wet sheen. |
| `lamination` | Directional bedding mask; shaly alone does not require stripes. |
| `cleavage` | Independent planar splitting direction; may intersect bedding. |
| `rounded_clasts` | Rounded clast mask over host matrix; independent size, sorting, and coverage. |
| `angular_clasts` | Angular fragment mask; retain recognizable angular boundaries. |
| `shell_fragments` | Shell-shaped masks; large fragments/cavities may require geometry. |
| `ooids` | Coated rounded grains; use only when an oolitic interpretation is warranted. |
| `carbonate_grains` | Generic carbonate grains; do not assume shells or ooids. |
| `mud_supported` | Disperse grains in a continuous supporting carbonate-mud matrix. |
| `grain_supported` | Touching grains; distinguish retained carbonate mud from cement/voids. |
| `bound_framework` | Verified binding/baffling/framework fabric; do not assume rigid coral. |
| `coal_lignite_variant` | Separate palette/finish preset; woody detail only when supported. |
| `surface_sheen` | Local roughness/smoothness; does not imply metal or transparency. |
| `banding` | Directional/warped bands; independent scale and direction. |
| `lithic_fragments` | Rock fragments in matrix; shape/composition/size require separate choices. |
| `crystal_fragments` | Discrete crystal fragments; retain tuff matrix rather than intrusive fabric. |
| `glass_shards` | Glass-shard detail in tuff matrix; preserve fragmental appearance. |
| `welding` | Flatten/orient suitable pyroclastic patterns; a structural change. |
| `vesicles` | Cavity pattern; use geometry for actual holes/silhouettes. |
| `igneous_mineral_balance` | Change mineral-region palettes/patterns; retain fine/coarse fabric and distinct identity. |
| `granitic_clasts` | Fragmented granitic-derived particles in sediment; preserve clastic fabric. |
| `poor_sorting` | Broad particle sizes in independently controlled matrix. |
| `paleosol_features` | Verified soil features on a separately identified parent material. |
| `plasticity` | Metadata only; infer neither wetness nor gloss. |
| `pyrite_component` | Localized brassy metallic-looking grains/veins; host remains nonmetallic. |
| `glauconite_component` | Reference-based greenish grains/pellets on a known host. |
| `kaolinite_component` | Fine clay-mineral component; a pale illustrative appearance is optional. |
| `iron_mineral_component` | Resolve siderite versus limonite before assigning mineral appearance. |
| `phosphate_component` | Reference needed for mineral, grain, nodule, coating, or matrix appearance. |
| `recrystallization` | Adjust crystal boundaries/detail using reference scale. |
| `mica_flakes` | Platy dielectric detail; do not imply whole-rock foliation. |
| `muscovite_component` | Pale platy mica detail with dielectric sheen. |
| `biotitization` | Reference-based dark mica replacement/flecks on host. |
| `chloritization` | Reference-based chlorite replacement; no mandatory global green tint. |
| `sericitization` | Fine pale mica replacement; separate from clay/chalk mixture. |
| `silicification` | Silica replacement/cement; no mandatory visible veins or gloss increase. |
| `carbonate_component` | Unspecified carbonate component; mineral and occurrence remain unresolved. |
| `cement_unspecified` | Resolve natural cement versus drilling cement and its composition. |
| `contamination_status` | Metadata/status overlay; do not create a new host lithology. |
| `alteration_unspecified` | Metadata only until alteration process/mineral appearance is known. |
| `volcanic_component` | Volcanic ash/fragments within the independently identified sedimentary host. |
| `serpentine_component` | Resolve mineral occurrence versus rock before selecting a host treatment. |
| `dike_geometry` | Geometry metadata for a body; does not determine crystal size. |

## Required geological interpretation rules

- Treat base families as shared rendering assets, not scientific synonyms. Preserve each lithology ID and raw label even when appearances are identical.
- Treat Granite Wash as granitic-derived sediment, Coquina as shell-rich limestone, Chert as fine siliceous rock, and Bentonite as fine clay material.
- Preserve a fine matrix for marl mixtures. Large contrasting patches are an optional illustrative pattern, not implied by the name.
- Calcareous indicates calcium-carbonate material without identifying the exact mineral. Reserve `calcite_component` for explicit calcite or confirmed calcitic interpretations. Color and shine are not universal consequences of composition.
- Keep grain type separate from carbonate support fabric: wackestone has a supporting carbonate-mud matrix; packstone has touching/supporting grains with carbonate mud; grainstone lacks depositional carbonate mud but may contain later cement. Boundstone needs a specific binding/baffling/framework reference. Do not make every grainstone oolitic or every boundstone coral.
- Preserve fragmental textures for lithic, crystal-rich, and glassy tuff. Glassy Tuff is not massive Obsidian. Welding changes structural patterns, not only tint.
- Vary mineral regions within crystalline igneous families; preserve distinctions among granite, granodiorite, quartz monzonite, diorite, quartz diorite, and gabbro. Uniformly darkening one granite image is insufficient as the only implementation.
- Treat pebbles as a size descriptor with unresolved shape/composition; the B06 default is illustrative. Till uses poor sorting and a separate matrix. Paleosol requires a parent material before specific soil features are assigned.
- Do not infer wetness from Plastic Clay, foliation from Metamorphic, crystal size from Dike, or a natural rock composition from Cement Contam.
- Keep pure mineral/component entries usable without inventing an observed host. A demonstration host may be used only with an explicit preview-host label and must not be written into source geology.

## Demonstration scene and integration

Create a scene or deterministic Editor scene-builder with valid camera, lighting, materials, labels, and a compact UI using the project's installed UI/input stack. Include:

1. A gallery of B01-B25; mark B25 conditional and all generated assets illustrative.
2. A selector for all 180 labels showing raw label, interpretation status, recipe/base, unresolved information, and illustrative/reference asset status.
3. Texture/legend mode switching and a few relevant visual controls.
4. Adjacent geological blocks and a small layered cutaway showing exposed faces, plus a movable specimen for object/rest projection.
5. These representative recipes: Fine Sandstone, Coarse Sandstone, Calcareous Sandstone, Shaly Sandstone with optional lamination, Shale Green, Slaty Shale with independent cleavage, Argillaceous Limestone, Sandy and Calcareous Dolomite, Marl, Till, Coquina, Wackstone, Packstone, Grainstone, Lithic Tuff, Welded Tuff, Glassy Tuff, Chert - Tiger Stripe, Obsidian, and a pyrite component preview.
6. Distinct previews for Unknown, Not Available, No Sample, Others, a hostless modifier, and an opaque source code. Preserve interval geometry.

Demonstrate that the system can be applied to an existing Renderer/submesh through a small documented API call; do not restrict it to the generated gallery. Keep runtime boolean slicing, geological reconstruction, crystal refraction, and complex volume rendering as explicit future extensions unless the existing project already requires them.

## Validation and acceptance criteria

Use the existing Unity test framework where present, plus focused Editor validation. Test substantive behavior and data integrity rather than creating tests that merely repeat implementation details.

- The seeded catalogue contains exactly 180 unique source labels and preserves the 182 source occurrences, including the two duplicate-label locations described above.
- All 25 base-family IDs and all 53 modifier IDs exist; every selected base, optional component, candidate, and modifier reference resolves.
- All 25 opaque code rows retain null selected bases, undecoded suffixes, and source-verification flags. Unverified normalized candidates do not become approved aliases.
- Resolver behavior covers exact labels, permitted case/whitespace lookup, invalid input, ambiguity, and unknown labels without losing the original string.
- Combined modifiers survive compilation, especially Sandy and Calcareous Dolomite and Argil and Sandy Dolomite. Metadata-only operations stay metadata. Optional shaly lamination does not silently become a required stripe pattern.
- Re-running initialization does not duplicate records, regenerate persistent IDs/GUIDs, erase confirmed overrides, or replace assigned real textures.
- Every selection produces a valid material or intentional status/component fallback. No missing scripts, broken references, or pink/error materials.
- Actual shaders compile for the detected Unity version and pipeline. Runtime assemblies do not depend on UnityEditor.
- On generated previews and assets supporting separated scales, changing grain scale leaves bedding/fracture scale unchanged. Unsupported controls are visibly unavailable for inseparable real scans. Bedding and cleavage can differ. Object/rest projection stays attached during motion, and geological projection aligns appropriately across adjacent units.
- Normal direction and component blending are correct on multiple faces and under changing light. Zero-strength components restore the host. Rounded clasts, angular clasts, shells, and ooids have distinct implemented shapes.
- Repeated applying/switching recipes does not cause unbounded material creation or change unrelated renderers. Owned runtime resources are cleaned up.
- Generated texture edges tile continuously; normal, color, and component patterns agree.

If the Editor is available, import/compile, run applicable checks, open the demo, and visually inspect representative cases. Use the existing Editor process or appropriate batch tooling without opening a conflicting second instance of the same project. A `-nographics` run is not visual verification. If rendering or Unity execution is unavailable, report the limitation precisely and provide reproducible setup/verification commands; do not claim those checks passed.

## Primary references for implementation and interpretation

Use the installed Unity version as authoritative; the Unity 6 / package 17 links below are reference starting points, not a requirement to upgrade. They support the API/import/projection requirements above. Geological references support terminology; illustrative recipes remain design choices.

- [BGS Rock Classification Scheme](https://www.bgs.ac.uk/technologies/bgs-rock-classification-scheme/) — Root names and qualifiers; shared visual bases must not replace geological classifications.
- [Kansas Geological Survey: Classification of Sedimentary Rocks](https://www.kgs.ku.edu/General/Class/sedimentary.html) — Fine sediment distinctions and sedimentary rock families.
- [Kansas Geological Survey: Igneous and Metamorphic Rocks in Geological Log Analysis](https://www.kgs.ku.edu/Publications/Bulletins/LA/09_igneous.html) — Granite wash as arkosic detrital sediment.
- [BGS: Chert](https://webapps.bgs.ac.uk/bgsrcs/rcs_details.cfm?code=CHRT) — Chert as a dense fine siliceous rock.
- [NOAA: What are coquina and tabby?](https://oceanservice.noaa.gov/facts/coquina-tabby.html) — Coquina as shell-fragment limestone.
- [Bureau of Economic Geology: Dunham's Carbonate Rock Texture Classification](https://www.beg.utexas.edu/lmod/_IOL-CM01/103103_PREFINAL/cm01-step03.htm) — Carbonate grain support, carbonate mud, and binding structures.
- [National Park Service: Igneous Rocks](https://www.nps.gov/subjects/geology/igneous.htm) — Intrusive versus volcanic fabrics; composition and crystal populations.
- [National Park Service: How Rocks are Classified](https://www.nps.gov/subjects/geology/how-rocks-are-classified.htm) — Metamorphic rocks include foliated and nonfoliated rocks.
- [USGS: Lithologic classification of geologic map units](https://apps.usgs.gov/thesaurus/thesaurus-full.php?thcode=4) — Geological terms including bentonite and broad material classes.
- [BGS Lexicon: Till](https://webapps.bgs.ac.uk/lexicon/lexicon.cfm?pub=TILL) — Till as unsorted glacial sediment.
- [University of Cambridge mineral collections: Pyrite](https://p1acollections.esc.cam.ac.uk/p1acollections/exhibits/show/minerals/pyrite.html) — Pyrite's metallic lustre; supports a local metallic-looking inclusion preset.
- [National Park Service: Minerals](https://www.nps.gov/subjects/geology/minerals.htm) — Rock-forming mineral appearances and distinction from whole-rock lithologies.
- [USGS: Munsell color value as related to organic carbon in Devonian shale](https://www.usgs.gov/publications/munsell-color-value-related-organic-carbon-devonian-shale-appalachian-basin) — Counterexample to assuming calcareous always makes shale lighter.
- [BGS: Quartz-latite](https://webapps.bgs.ac.uk/bgsrcs/rcs_details.cfm?code=QLA) — Fine-grained quartz-latite fabric; broad asset-family sharing does not make distinct rocks synonymous.
- [Unity Shader Graph 17.0: Triplanar Node](https://docs.unity3d.com/Packages/com.unity.shadergraph@17.0/manual/Triplanar-Node.html) — Projection coordinate spaces, three texture samples per map, normal map mode.
- [Unity Shader Graph 17.0: Normal Blend Node](https://docs.unity3d.com/Packages/com.unity.shadergraph@17.0/manual/Normal-Blend-Node.html) — Reoriented normal blending for added detail.
- [Unity 6: Introduction to normal maps](https://docs.unity3d.com/6000.0/Documentation/Manual/StandardShaderMaterialParameterNormalMap.html) — Normal maps affect shading; Unity uses Y+ normal maps.
- [Unity 6: Assign a channel-packed texture to a URP material](https://docs.unity3d.com/6000.0/Documentation/Manual/urp/shaders-in-universalrp-channel-packed-texture.html) — URP Lit channel packing and linear-data import.
- [Unity HDRP 17.0: Mask and detail maps](https://docs.unity3d.com/Packages/com.unity.render-pipelines.high-definition@17.0/manual/Mask-Map-and-Detail-Map.html) — HDRP Mask Map channel packing.
- [Unity 6: MaterialPropertyBlock](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/MaterialPropertyBlock.html) — MaterialPropertyBlock is incompatible with the SRP Batcher.
- [Unity: active render pipeline](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Rendering.GraphicsSettings-currentRenderPipeline.html) — Graphics/Quality pipeline selection.
- [Unity: Renderer.material](https://docs.unity3d.com/6000.0/Documentation/ScriptReference/Renderer-material.html) — Material instantiation and ownership.
- [Unity: JSON serialization](https://docs.unity.com/en-us/engine/6000.0/manual/programming-environment/code-reload-serialization/script-serialization/json-serialization) — Supported serialization and ScriptableObject loading.
- [Unity: Editor command-line arguments](https://docs.unity3d.com/6000.0/Documentation/Manual/EditorCommandLineArguments.html) — Batch-mode and graphics-device verification limitations.

## Documentation and final handoff

Write a concise README covering setup, actual supported Unity/pipeline version, menu commands, runtime API, texture replacement, map conventions, scale/orientation controls, recipe composition, visual defaults, and unresolved-source workflow. Include the base texture procurement checklist, modifier vocabulary, catalogue source, and known limitations.

In your final response, state what you implemented, the relevant project paths, how to open/run the demo, how to apply a lithology to my meshes, and how to replace preview textures. Report actual test/compile/visual outcomes separately from unexecuted checks. Identify remaining source-data ambiguities without treating their deliberate fallback handling as an unfinished implementation.

Start by inspecting the workspace, then implement and verify the system.