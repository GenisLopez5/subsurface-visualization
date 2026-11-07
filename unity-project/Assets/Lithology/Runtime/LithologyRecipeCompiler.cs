using System;
using System.Collections.Generic;
using UnityEngine;

namespace Subsurface.Lithology
{
    public sealed class CompiledRecipe
    {
        public LithologyMaterialRecipe source;
        public BaseTextureSet texture;
        public CoordinateSettings coordinates;
        public StructuralSettings structure;
        public Color tint, light, dark, legendColor;
        public float grain, exaggeration, normal, smoothness, support, sorting, mineralBalance;
        public int seed, statusPattern;
        public bool schematic, proceduralDark, valid = true;
        public readonly List<ComponentLayer> layers = new List<ComponentLayer>();
        public readonly List<string> diagnostics = new List<string>();
    }
    public static class LithologyRecipeCompiler
    {
        public static uint StableSeed(string s)
        { unchecked { uint h=2166136261; foreach(char c in s ?? "") { h ^= c; h *= 16777619; } return h; } }
        static T Copy<T>(T value) => JsonUtility.FromJson<T>(JsonUtility.ToJson(value));
        public static CompiledRecipe Compile(LithologyCatalog catalog, LithologyDefinition definition,
            VisualOverrides visual = null, IList<ModifierSettings> hostModifiers = null)
        {
            var r = new CompiledRecipe();
            var recipe = definition != null ? definition.EffectiveRecipe : null;
            r.source = recipe;
            r.coordinates = recipe != null ? Copy(recipe.coordinates) : new CoordinateSettings();
            r.structure = recipe != null ? Copy(recipe.structure) : new StructuralSettings();
            r.texture = recipe != null ? recipe.baseTexture : null;
            r.tint = recipe != null ? recipe.palette : Color.white;
            r.light = recipe != null ? recipe.mineralLight : Color.white;
            r.dark = recipe != null ? recipe.mineralDark : Color.gray;
            r.mineralBalance = recipe != null ? recipe.mineralDarkCoverage : .3f;
            r.grain = recipe != null ? recipe.grainMetres : .002f;
            r.exaggeration = recipe != null ? recipe.schematicExaggeration : 8;
            r.normal = recipe != null ? recipe.normalStrength : .4f;
            r.smoothness = recipe != null ? recipe.smoothness : .2f;
            r.seed = recipe != null ? recipe.seed : 101;
            r.schematic = r.texture == null && (definition == null || definition.role != RenderRole.Procedural);
            r.proceduralDark = definition != null && definition.role == RenderRole.Procedural;
            r.statusPattern = definition == null ? 1 : definition.role == RenderRole.DataStatus ?
                definition.rawLabel == "Unknown" ? 1 : definition.rawLabel == "Not Available" ? 2 : definition.rawLabel == "No Sample" ? 3 : definition.rawLabel == "Others" ? 4 : 5 : (int)definition.role + 6;
            uint hash = StableSeed(definition != null ? definition.stableId : "UNKNOWN");
            r.legendColor = Color.HSVToRGB((hash % 360)/360f,.28f,.76f);
            if(definition!=null&&definition.role==RenderRole.DataStatus)
                r.legendColor=definition.rawLabel=="Unknown"?new Color(.85f,.68f,.24f):definition.rawLabel=="Not Available"?new Color(.40f,.57f,.67f):definition.rawLabel=="No Sample"?new Color(.64f,.48f,.75f):definition.rawLabel=="Others"?new Color(.75f,.55f,.38f):new Color(.92f,.45f,.16f);
            if (r.schematic) r.diagnostics.Add("Labeled schematic fallback: " + (definition != null ? definition.role.ToString() : "unknown input") + "; no host inferred.");
            if (recipe == null && definition != null) { r.valid=false; r.diagnostics.Add("Missing recipe; safe schematic fallback."); }
            if (recipe != null)
            {
                foreach(var layer in recipe.componentLayers) r.layers.Add(Copy(layer));
                foreach(var m in recipe.modifiers) Apply(catalog,r,m,visual);
            }
            if (hostModifiers != null)
            {
                if (r.texture == null) r.diagnostics.Add("Host modifiers retained, but no known physical host was supplied.");
                foreach(var m in hostModifiers) Apply(catalog,r,m,visual);
            }
            if (visual != null)
            {
                if (visual.overridePalette) r.tint *= visual.tint;
                r.grain *= Mathf.Max(.001f,visual.grainMultiplier);
                r.exaggeration *= Mathf.Max(.001f,visual.exaggerationMultiplier);
                r.normal *= visual.normalMultiplier;
                if (visual.overrideCoordinates) r.coordinates = Copy(visual.coordinates);
            }
            if(definition!=null)
                foreach(var l in r.layers)
                {
                    string family=l.semanticId=="clay_content"?"B01":l.semanticId=="rounded_clasts"?"B06":l.semanticId=="angular_clasts"?"B07":l.semanticId=="dolomite_component"?"B09":l.semanticId=="carbonate_grains"?"B12":l.semanticId=="volcanic_component"?"B18":l.semanticId=="calcareous_component"?"B08":"";
                    if(l.textureSource==null)l.textureSource=definition.optionalComponentSources.Find(s=>s!=null&&s.stableId==family);
                }
            r.layers.RemoveAll(l=>l.coverage<=0);
            if (r.layers.Count > 2) { r.valid = false; r.schematic = true; r.diagnostics.Add("Layer limit exceeded ("+r.layers.Count+"). All layers preserved. Author a baked texture set / consolidated component layer in the catalogue window; runtime refuses truncation."); }
            if (r.texture != null && r.texture.conditional && (definition == null || definition.confirmedRecipe == null || !definition.sourceVerified))
            { r.valid=false; r.schematic=true; r.diagnostics.Add("Conditional B25 requires a separately confirmed rock interpretation and source verification."); }
            ValidateNumbers(r);
            return r;
        }
        static void ValidateNumbers(CompiledRecipe r)
        {
            bool bad = !Positive(r.grain) || !Positive(r.exaggeration) || !Positive(r.coordinates.metresPerUnityUnit) ||
                !Positive(r.structure.beddingSpacingMetres) || !Positive(r.structure.cleavageSpacingMetres) ||
                !Positive(r.structure.clastSpacingMetres) || !Positive(r.structure.fractureSpacingMetres) || !Positive(r.structure.veinWidthMetres) || !Positive(r.structure.mottleSpacingMetres) ||
                !Finite(r.coordinates.geologicalOrigin)||!Finite(r.coordinates.beddingNormal)||!Finite(r.coordinates.cleavageNormal)||!Finite(r.tint)||!Finite(r.light)||!Finite(r.dark)||
                !Finite(r.normal)||!Finite(r.smoothness)||!Finite(r.mineralBalance)||r.coordinates.beddingNormal.sqrMagnitude < 1e-8f || r.coordinates.cleavageNormal.sqrMagnitude < 1e-8f;
            foreach(var l in r.layers)if(!Positive(l.spacingMetres)||!Finite(l.coverage)||!Finite(l.smoothness)||!Finite(l.metallic)||!Finite(l.relief)||!Finite(l.color)||!Enum.IsDefined(typeof(MaskKind),l.mask))bad=true;
            if (bad) { r.valid=false; r.schematic=true; r.diagnostics.Add("Invalid spacing/direction. Safe schematic fallback; repair the recipe."); }
            r.grain = Safe(r.grain); r.exaggeration=Safe(r.exaggeration);
            r.coordinates.metresPerUnityUnit=Safe(r.coordinates.metresPerUnityUnit);
            r.structure.beddingSpacingMetres=Safe(r.structure.beddingSpacingMetres);r.structure.cleavageSpacingMetres=Safe(r.structure.cleavageSpacingMetres);r.structure.fractureSpacingMetres=Safe(r.structure.fractureSpacingMetres);r.structure.clastSpacingMetres=Safe(r.structure.clastSpacingMetres);r.structure.veinWidthMetres=Safe(r.structure.veinWidthMetres);r.structure.mottleSpacingMetres=Safe(r.structure.mottleSpacingMetres);
            if(!Finite(r.coordinates.geologicalOrigin))r.coordinates.geologicalOrigin=Vector3.zero;
            if(!Finite(r.coordinates.beddingNormal))r.coordinates.beddingNormal=Vector3.up;if(!Finite(r.coordinates.cleavageNormal))r.coordinates.cleavageNormal=Vector3.right;
            if(!Finite(r.tint))r.tint=Color.white;if(!Finite(r.light))r.light=Color.white;if(!Finite(r.dark))r.dark=Color.gray;
            if(!Finite(r.normal))r.normal=.5f;if(!Finite(r.smoothness))r.smoothness=.2f;if(!Finite(r.mineralBalance))r.mineralBalance=.3f;
            r.coordinates.beddingNormal = r.coordinates.beddingNormal.sqrMagnitude>1e-8f ? r.coordinates.beddingNormal.normalized : Vector3.up;
            r.coordinates.cleavageNormal = r.coordinates.cleavageNormal.sqrMagnitude>1e-8f ? r.coordinates.cleavageNormal.normalized : Vector3.right;
        }
        static bool Positive(float f)=>f>0 && !float.IsNaN(f) && !float.IsInfinity(f);
        static bool Finite(float f)=>!float.IsNaN(f)&&!float.IsInfinity(f);
        static bool Finite(Vector3 v)=>Finite(v.x)&&Finite(v.y)&&Finite(v.z);
        static bool Finite(Color v)=>Finite(v.r)&&Finite(v.g)&&Finite(v.b)&&Finite(v.a);
        static float Safe(float f)=>Positive(f)?f:.001f;
        static void Apply(LithologyCatalog catalog,CompiledRecipe r,ModifierSettings m,VisualOverrides visual)
        {
            var d = catalog != null ? catalog.GetModifier(m.id) : null;
            if (d == null) { r.valid=false; r.schematic=true; r.diagnostics.Add("Unknown semantic modifier: "+m.id); return; }
            if (!m.enabled || m.strength <= 0) return;
            if (m.optional && !(m.referenceConfirmed || (m.id=="lamination" && visual != null && visual.optionalLamination)))
            { r.diagnostics.Add(m.id+": optional, disabled pending reference or explicit display override."); return; }
            if (d.requiresReference && !m.referenceConfirmed)
            { r.diagnostics.Add(m.id+": metadata pending occurrence/mineral/reference confirmation."); return; }
            if (d.operation==ModifierOperation.Metadata || d.operation==ModifierOperation.Geometry || d.operation==ModifierOperation.Conditional)
            { r.diagnostics.Add(m.id+": "+d.operation+"; "+d.constraint); return; }
            if (d.operation==ModifierOperation.Status) { r.statusPattern=5; r.diagnostics.Add("Sample contamination overlay; host retained."); return; }
            if (r.schematic) { r.diagnostics.Add(m.id+": retained without inventing a host."); return; }
            string v=m.value??"";
            switch(d.operation)
            {
                case ModifierOperation.Palette:
                    if(v=="green") r.tint*=new Color(.52f,.69f,.50f);
                    else if(v=="brown") r.tint*=new Color(.65f,.46f,.31f);
                    else if(v=="gray") r.tint*=new Color(.7f,.72f,.73f);
                    else if(v.Contains("light/dark")) { r.light=Color.white; r.dark=new Color(.05f,.06f,.06f); }
                    else r.diagnostics.Add("Palette unspecified; artistic default retained."); break;
                case ModifierOperation.GrainScale: r.grain = v=="fine"?.00025f:v=="coarse"?.0015f:v=="medium"?.00065f:v=="pebble-sized"?.02f:r.grain; break;
                case ModifierOperation.Consolidation: r.structure.fractures=v=="consolidated"?.2f:0; r.normal*=v=="unconsolidated"?1.15f:1; break;
                case ModifierOperation.Bedding: r.structure.bedding=Mathf.Max(r.structure.bedding,m.strength); break;
                case ModifierOperation.Cleavage: r.structure.cleavage=Mathf.Max(r.structure.cleavage,m.strength); break;
                case ModifierOperation.Banding: r.structure.banding=Mathf.Max(r.structure.banding,m.strength); break;
                case ModifierOperation.Sheen: r.smoothness=v=="glassy"?.82f:v=="porcelain-like"?.55f:.65f; break;
                case ModifierOperation.Welding: r.structure.welding=m.strength; break;
                case ModifierOperation.Sorting: r.structure.poorSorting=m.strength; break;
                case ModifierOperation.Recrystallization: r.grain*=1.8f; break;
                case ModifierOperation.SupportFabric:
                    r.structure.grainSupport=m.id=="mud_supported"?.25f:.85f;
                    r.structure.carbonateMudPresent = !v.Contains("no carbonate mud"); break;
                case ModifierOperation.MineralBalance:
                    // Display mineral populations, explicitly not measured modal percentages.
                    uint h=StableSeed(r.source.stableId); r.light=Color.Lerp(new Color(.62f,.60f,.54f),new Color(.94f,.87f,.8f),(h%17)/16f);
                    r.dark=Color.Lerp(new Color(.035f,.045f,.05f),new Color(.24f,.26f,.24f),((h/17)%13)/12f); break;
                case ModifierOperation.Component:
                    var layer = Layer(m.id,m.occurrence,m.strength,r);
                    // An explicitly authored layer with this semantic ID takes precedence.
                    if (!r.layers.Exists(x=>x.semanticId==m.id)) r.layers.Add(layer);
                    break;
            }
        }
        static ComponentLayer Layer(string id,MaskKind mask,float strength,CompiledRecipe r)
        {
            var l=new ComponentLayer { semanticId=id,mask=mask,coverage=strength,spacingMetres=r.grain,smoothness=.22f };
            switch(id)
            {
                case "clay_content": l.color=new Color(.45f,.37f,.27f); l.spacingMetres=.00015f; break;
                case "silt_content": l.color=new Color(.61f,.56f,.43f); l.spacingMetres=.00004f; break;
                case "sand_content": l.color=new Color(.74f,.64f,.44f); l.spacingMetres=.0006f; break;
                case "organic_content": l.color=new Color(.045f,.035f,.025f); break;
                case "pyrite_component": l.color=new Color(.72f,.52f,.13f); l.metallic=.88f; l.smoothness=.72f; break;
                case "glauconite_component": l.color=new Color(.23f,.37f,.19f); break;
                case "mica_flakes": case "muscovite_component": case "sericitization": l.color=new Color(.75f,.73f,.63f); l.smoothness=.7f; break;
                case "biotitization": l.color=new Color(.10f,.09f,.07f); l.smoothness=.5f; break;
                case "chloritization": l.color=new Color(.35f,.45f,.33f); break;
                case "volcanic_component": l.color=new Color(.43f,.39f,.33f); break;
                case "glass_shards": l.color=new Color(.37f,.42f,.4f); l.smoothness=.65f; break;
                case "rounded_clasts": case "angular_clasts": case "granitic_clasts": case "lithic_fragments": l.color=new Color(.50f,.46f,.39f); l.spacingMetres=r.structure.clastSpacingMetres; break;
                case "shell_fragments": l.color=new Color(.86f,.80f,.63f); l.spacingMetres=.012f; break;
                case "vesicles": l.color=new Color(.055f,.045f,.04f); l.spacingMetres=.008f; l.relief=-.6f; break;
                default: l.color=new Color(.73f,.71f,.63f); break;
            }
            // Resource sources remain separate from host and never select candidate bases.
            return l;
        }
    }
}
