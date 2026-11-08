# Lithology material system

Target: **Unity 6000.5.9f1, Universal RP 17.5.0**, as found in this project. Both Mobile and PC quality overrides use URP. This implementation preserves the pipeline and quality settings. Editor control for verification uses `com.unity.pipeline` (tooling only); runtime has no dependency on it or UnityEditor.

## Start here

Use **Tools → Lithology → Bootstrap Complete System** once. It initializes 180 distinct identities, 25 base families and 53 modifiers; generates deterministic illustrative PBR previews; creates texture and legend materials; saves and opens the demo additively. Existing unsaved scenes remain open. Subsequent runs update assets in place and reuse the demo. Do not discard unsaved work to use this system.

Open **Tools → Lithology → Open Demo Scene**, then Play. Families, Recipes, and Cutaway buttons frame the three exhibits. The sidebar selects all 180 labels, switches legend mode, changes grain/exaggeration/normal controls, enables optional lamination, and moves the object-rest specimen. Text labels identify schematic states. B25 and D11 are conditional diagnostics, never assumed serpentinite/coral.

Generated output: `Assets/Lithology/Generated`. Catalogue: `LithologyCatalog.asset`. Demo: `Demo/LithologyDemo.unity`. Runtime scripts and shader are separate from Editor tooling and tests. Generated maps/materials are replaceable assets, not photoreal scans or measured geology.

Other menu commands initialize/update only, generate previews/materials, validate, open the searchable catalogue/texture authoring window, and pack a selected conventional roughness map into a linear surface map. Preview resolution defaults to 128; call `LithologyPreviewGenerator.Generate(256)` from an Editor script for another size (32–512).

## Apply to any mesh or material slot

Attach `LithologyRenderer`, assign the explicit catalogue and target Renderer, select the material slot, and set a definition or raw label. Other material slots remain intact. The catalogue window also applies a selected identity to selected Renderers at slot 0 with Undo.

This compiling integration component illustrates the public API (also supplied in `Runtime/LithologyIntegrationExample.cs`):

```csharp
using Subsurface.Lithology;
using UnityEngine;

public class Example : MonoBehaviour
{
    public LithologyCatalog catalogue;
    public LithologyRenderer surface;
    void Start()
    {
        var result = catalogue.Resolve("  fine sandstone  ");
        Debug.Log(result.status + ": " + result.originalInput);
        surface.catalog = catalogue;
        surface.materialSlot = 0;
        surface.SetDefinition(result.definition);
        surface.SetOverrides(new VisualOverrides { grainMultiplier = 1.5f });
        surface.SetDisplayMode(DisplayMode.Texture);
    }
}
```

Use `surface.SetLabel(raw)` to retain unrecognized input and show its diagnostic fallback. To demonstrate a component on a known host, select that host and append `new ModifierSettings { id="pyrite_component", strength=.4f }` to `hostModifiers`, then `Apply()`. This never writes a host into the standalone Pyrite source record.

For direct material use, own a `LithologyMaterialCache`, call `Acquire(catalogue, definition, mode, overrides, modifiers)`, assign the lease's `Material` through `sharedMaterials`, and dispose the lease when it is no longer assigned. Dispose the cache after its users. `LithologyRenderer` handles these lifetimes and restores the original slot when disabled. Never modify leased materials. No `Renderer.material` calls or per-frame material generation occur. `Apply` is explicit/change-driven; `OnValidate` only schedules one later application.

## Identity and source uncertainty

`Seed/CatalogueSeed.json` is the complete checked-in seed. `Documentation/SourceBrief.md` retains the entire supplied mapping, vocabulary, procurement guidance, interpretation constraints, and primary reference links. No attachment, PDF, network request, or external scan pack is needed at runtime or bootstrap.

Stable source IDs L001–L180 follow the supplied mapping row order; B01–B25 and the 53 semantic IDs are explicit. Duplicate occurrences are stored on the two corresponding definitions, yielding 182 source locations. Unknown original extraction categories/PDF filenames/pages/codebooks stay `Unknown`. Four Chert prefix expansions are recorded. Provisional name candidates are never approved aliases. Only aliases with verification evidence participate in lookup; exact labels take precedence and normalized/alias collisions return ambiguity. Punctuation and compound identities remain distinct.

Null bases dispatch by role: procedural Tar/Bitumen, hostless components, descriptors, context-dependent materials, generic classes, data states, and undecoded source codes. They render opaque labeled schematic states without removing interval geometry. `Unknown`, `Not Available`, `No Sample`, `Others`, and contamination have distinct patterns. All 25 opaque codes remain null-base and require their source legend. Boundstone, Serpentine, Cement, and broad classes keep their ambiguity deliberately.

Initialization updates generated records/assets at stable paths, preserves GUIDs, real texture assignments, aliases, source verification/evidence, and `confirmedRecipe`. Use **Create editable override** in the catalogue window to preserve authored recipe/interpretation changes; edits to generated defaults are intentionally regenerated. Confirming a recipe and verifying geological source evidence are separate decisions. B25 additionally requires both a confirmed recipe and `sourceVerified`.

## Replace previews with PBR scans

Open **Catalogue and Texture Authoring**, search/filter a source label, and edit its shared B family maps. Replacing a B family updates every recipe using it without merging identities. Assign source URL, license, scale evidence, and asset status. Keep real assets outside `Generated/PreviewTextures`; regeneration writes only the owned preview paths and replaces only missing or owned map references. `Documentation/TextureProcurement.tsv` lists every B family, exact search guidance, maps, assignments, scale assumptions, provenance and license fields. Prefer fresh/core/cut-face references; weathering is a separate treatment.

- Albedo and grain color: sRGB. Normal maps: import as Normal Map, Unity Y+/OpenGL. Enable `invertNormalGreen` to convert an opposite Y-/DirectX convention in shader.
- Custom packed surface: **R metallic, G AO, B unused, A smoothness**, linear import. Conventional roughness becomes `1 - roughness`. The menu packing utility creates dielectric R=0, AO=1; replace those channels with real data if available. Bulk host remains dielectric; explicit local components such as pyrite may be metallic.
- Masks, AO, height and packed data: linear. Missing maps use neutral textures/strength flags. Height is retained for future geometry workflows, and is **not displacement** in this shader.
- Independent grain/detail albedo, normal and region masks have their own scale. `detailTileMetres` is a relative scale calibration (0.01 m nominal); recipe `grainMetres × schematicExaggeration × detailTileMetres / 0.01` sets the repeat period. Detail textures contain multiple illustrative grains per tile; this is a display scale, not grain metrology.
- For scans with baked grains/cracks, set `independentGrainScale=false`. Macro scale stays fixed and independent detail controls are disabled. Separate grain channels can be assigned later. `hasSeparatedMacro`, status and license fields describe capabilities; unsupported height displacement remains false.
- Apply map import conventions with the window button, then refresh the material preview. Increment a texture set/recipe `revision` after editing texture pixels in place; reference/parameter changes already participate in material cache identity.

## Coordinates, composition, and performance

Default physical assumption: **1 Unity unit = 1 metre**. All numerical defaults, mask coverages, palettes, mineral population choices and exaggeration are artistic. They are not measured geology. World geological coordinates align across static adjacent units; geological origin is specified in Unity units. Object/rest coordinates anchor patterns to the mesh for movable/exploded specimens, including negative/nonuniform scales. Object-local distances follow rest object units, so object scale intentionally stretches their apparent world dimensions. Bedding and cleavage have separate normalized directions and positive spacings in the chosen frame. UV projection requires UV0 and tangents; missing channels cause a diagnostic triplanar fallback. Triplanar needs only normals, with no tangent requirement.

Composition is deterministic: recipe base/palette and authored layers; semantic modifiers in source order (explicit authored layer with the same semantic ID wins); supplied host modifiers; visual overrides last. Structural strengths take the maximum. Grain/sheen and support-fabric settings use their explicit operations in order. Optional `lamination?` stays disabled unless reference-confirmed or explicitly enabled as a display override. Multiple independently resolved components survive compilation. Pure metadata/geometry and unresolved conditional operations report their status without inventing geology.

Two active component layers are supported. More layers remain in the compiled data and trigger an obvious schematic fallback with a layer-limit diagnostic. The window describes the authoring route: bake the excess composition into a new PBR texture set externally, retain the full source recipe as provenance, and reference the baked set through a confirmed override with at most two live layers. No automatic general-purpose geological baker is claimed.

Components use the same masks for color, decoded normal-region transitions, smoothness, metallic and AO. Zero coverage is exact host identity. Added detail uses RNM in a shared normal frame; triplanar normals use signed surface gradients and correct inverse-transpose object normal conversion. Bedding, cleavage, fractures, clasts, vein width and mottling have separate scales. Mipmapped/trilinear preview maps and derivative fade suppress subpixel grain patterns. Preview texture generation uses documented local integer seeds `17001 + B-number*997` (D groups 31991), periodic fields and matching edge/normal samples; it never changes global Random state.

The shader implements opaque URP PBR forward-only lighting plus ShadowCaster, DepthOnly and shaded DepthNormals passes. It uses `UnityPerMaterial` layout across passes; this makes SRP batching possible but does not promise batching or fewer draw calls. No MaterialPropertyBlock is used. A triplanar map costs three samples; each active component can add three maps. Profile on target hardware before increasing layers/resolution. The cache shares identical effective recipes and keeps at most 48 unused materials by default, plus active leases; full serialized identity includes recipe/texture-set state, seeds, mode, frame and overrides. Domain reload and disabled-domain-reload play transitions clear owned material state.

## Verification and boundaries

Run **Validate Catalogue and Materials** and the **Lithology.Tests** EditMode suite in Test Runner. `Generated/Verification` contains actual validation/test/render evidence when executed. A reproducible live Editor CLI call is:

```powershell
unity command script eval --caller plugin --skill unity-cli --code 'Subsurface.Lithology.Editor.LithologyBuilder.Bootstrap();'
unity command test run --caller plugin --skill unity-cli --mode editor --filter Lithology.Tests --filter-type assembly
```

The shader decorates surfaces with 2D triplanar maps and procedural masks. It does not establish coherent individual clasts through arbitrary volume slices. The demo uses real solid stepped meshes/caps, not clipping without geometry. True volume fields, runtime boolean slicing/reconstruction, displacement/silhouette holes, crystal refraction, complex volume rendering, lightmap baking/Meta pass and automatic multi-layer baking are future extensions. The current scene uses real-time lights and ambient probes. No photorealism or specimen accuracy is claimed.

Implementation references: installed URP 17.5 `Lighting.hlsl`, `LitForwardPass.hlsl`, `ShadowCasterPass.hlsl`; [Unity triplanar normal projection](https://docs.unity3d.com/Packages/com.unity.shadergraph@17.0/manual/Triplanar-Node.html), [normal blending](https://docs.unity3d.com/Packages/com.unity.shadergraph@17.0/manual/Normal-Blend-Node.html), and [UI Document](https://docs.unity3d.com/6000.0/Documentation/Manual/UIE-create-ui-document-component.html). Geological terminology references are preserved with their exact source links in the supplied brief.
