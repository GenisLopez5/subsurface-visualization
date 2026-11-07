using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using UnityEditor;
using UnityEngine;

namespace Subsurface.Lithology.Editor
{
    public static class LithologyBuilder
    {
        public const string Root="Assets/Lithology", Output=Root+"/Generated", CatalogPath=Output+"/LithologyCatalog.asset";
        public static LithologyCatalog Catalog => AssetDatabase.LoadAssetAtPath<LithologyCatalog>(CatalogPath);
        static T Asset<T>(string path) where T:ScriptableObject
        {
            var a=AssetDatabase.LoadAssetAtPath<T>(path);
            if(a!=null)return a;
            string folder=Path.GetDirectoryName(path);if(!Directory.Exists(folder)){Directory.CreateDirectory(folder);AssetDatabase.Refresh();}
            a=ScriptableObject.CreateInstance<T>();AssetDatabase.CreateAsset(a,path);return a;
        }
        [MenuItem("Tools/Lithology/Bootstrap Complete System",priority=0)]
        public static void Bootstrap() { Initialize();LithologyPreviewGenerator.Generate();GenerateMaterials();LithologyDemoBuilder.Create();LithologyValidation.Validate(); }
        [MenuItem("Tools/Lithology/Initialize or Update Catalogue",priority=10)]
        public static void Initialize()
        {
            var seed=JsonUtility.FromJson<SeedData>(File.ReadAllText(Root+"/Seed/CatalogueSeed.json"));
            if(seed==null||seed.rows.Length!=180||seed.bases.Length!=25||seed.modifiers.Length!=53)throw new InvalidDataException("Seed count/schema mismatch");
            if(seed.rows.Select(r=>r.id).Distinct().Count()!=180||seed.rows.Select(r=>r.rawLabel).Distinct().Count()!=180||seed.rows.Sum(r=>r.occurrences)!=182)throw new InvalidDataException("Identity/source occurrence mismatch");
            var c=Asset<LithologyCatalog>(CatalogPath);Undo.RecordObject(c,"Update lithology catalogue");c.shader=Shader.Find("Lithology/URP Lithology Lit");
            var bases=new Dictionary<string,BaseTextureSet>();
            foreach(var b in seed.bases)
            {
                var set=Asset<BaseTextureSet>(Output+"/Bases/"+b.id+".asset");Undo.RecordObject(set,"Update base metadata");
                set.stableId=b.id;set.description=b.family;set.procurementGuidance=b.guidance;set.conditional=b.id=="B25";
                bases.Add(b.id,set);if(!c.bases.Contains(set))c.bases.Add(set);EditorUtility.SetDirty(set);
            }
            foreach(var sm in seed.modifiers)
            {
                var m=Asset<ModifierDefinition>(Output+"/Modifiers/"+sm.id+".asset");m.stableId=sm.id;m.constraint=sm.instruction;Configure(m);
                if(!c.modifiers.Contains(m))c.modifiers.Add(m);EditorUtility.SetDirty(m);
            }
            c.RebuildLookups();
            foreach(var row in seed.rows)
            {
                var d=Asset<LithologyDefinition>(Output+"/Definitions/"+row.id+".asset");Undo.RecordObject(d,"Update source record");
                d.stableId=row.id;d.rawLabel=row.rawLabel;d.provisionalNormalizedName=row.normalizedCandidate;d.role=(RenderRole)Enum.Parse(typeof(RenderRole),row.role);
                d.sourceInstructions=row.instructions;d.requiresSourceLegend=d.role==RenderRole.SourceCode||row.instructions.Contains("V:");
                if(!d.sourceVerified)d.interpretation=d.requiresSourceLegend?InterpretationStatus.NeedsVerification:InterpretationStatus.Illustrative;
                d.prefixExpanded=row.prefixExpanded;d.sourceLocations.Clear();
                foreach(var loc in row.extractionLocations)d.sourceLocations.Add(new SourceLocation {mappingRow=row.mappingRow,briefLine=row.briefLine,extractionLocation=loc});
                d.optionalComponentSources=row.componentSourceIds.Select(id=>bases[id]).ToList();
                d.possibleButUnselectedBases=row.candidateBaseIds.Select(id=>bases[id]).ToList();
                bool newRecipe=d.generatedRecipe==null;
                var recipe=Asset<LithologyMaterialRecipe>(Output+"/Recipes/"+row.id+".asset");
                // Generated defaults update separately. User edits belong in confirmedRecipe and are never touched.
                recipe.stableId=row.id+"-default";recipe.baseTexture=string.IsNullOrEmpty(row.selectedBaseId)?null:bases[row.selectedBaseId];recipe.seed=17001+row.mappingRow*997;
                recipe.modifiers=ParseModifiers(row.instructions,c);recipe.componentLayers.Clear();recipe.coordinates=new CoordinateSettings();recipe.structure=new StructuralSettings();
                recipe.palette=Color.white;recipe.grainMetres=DefaultGrain(row.selectedBaseId);recipe.schematicExaggeration=8;recipe.normalStrength=.55f;recipe.smoothness=DefaultSmooth(row.selectedBaseId);
                recipe.mineralLight=new Color(.83f,.8f,.73f);recipe.mineralDark=new Color(.14f,.16f,.17f);
                recipe.mineralDarkCoverage=row.rawLabel=="Gabbro"||row.rawLabel=="Basalt"?.8f:row.rawLabel=="Diorite"||row.rawLabel=="Andesite"?.55f:row.rawLabel=="Quartz Diorite"||row.rawLabel=="Hornbl-Qtz-Dio"?.4f:row.rawLabel=="Granite"?.16f:row.rawLabel=="Quartz Monzonite"?.24f:.3f;
                if(row.rawLabel=="Coal-Lignite")recipe.palette=new Color(.42f,.27f,.16f);
                if(row.rawLabel=="Tar"||row.rawLabel=="Bitumen")recipe.smoothness=.7f;
                if(row.selectedBaseId=="B06"||row.selectedBaseId=="B07")recipe.structure.clastSpacingMetres=.12f;
                // Fine mineral balance is an illustrative population choice, not a classification claim.
                if(row.rawLabel=="Basalt"||row.rawLabel=="Gabbro")recipe.mineralDark=new Color(.035f,.045f,.05f);
                d.generatedRecipe=recipe;if(!c.definitions.Contains(d))c.definitions.Add(d);
                EditorUtility.SetDirty(recipe);EditorUtility.SetDirty(d);
            }
            c.revision++;c.RebuildLookups();EditorUtility.SetDirty(c);AssetDatabase.SaveAssets();
            WriteProcurement(c);Debug.Log("Lithology: initialized 180 identities / 182 occurrences, 25 families, 53 modifiers.");
        }
        public static List<ModifierSettings> ParseModifiers(string notes,LithologyCatalog c)
        {
            string section=notes.Split(';')[0];var list=new List<ModifierSettings>();
            foreach(string token in section.Split(','))
            {
                var bits=token.Trim().Split(new[]{'='},2);string id=bits[0].Trim().TrimEnd('?');
                var known=c.GetModifier(id);if(known==null) {if(Regex.IsMatch(id,@"^[a-z]+(?:_[a-z]+)+$"))throw new InvalidDataException("Unknown seed modifier: "+id);continue;}
                list.Add(new ModifierSettings {id=id,value=bits.Length>1?bits[1]:"",optional=bits[0].EndsWith("?"),occurrence=known.defaultMask,strength=DefaultStrength(id)});
            }
            return list;
        }
        static float DefaultStrength(string id)=>id=="clay_content"?.22f:id=="silt_content"?.16f:id=="sand_content"?.32f:id=="calcareous_component"?.22f:id=="dolomite_component"?.2f:id=="rounded_clasts"||id=="angular_clasts"?.65f:id=="shell_fragments"?.85f:id=="carbonate_grains"?.75f:id=="ooids"?.8f:id=="vesicles"?.8f:.4f;
        static float DefaultGrain(string id)=>id=="B01"||id=="B08"||id=="B10"?.00008f:id=="B03"?.00004f:id=="B06"||id=="B07"?.018f:id=="B11"?.006f:id=="B22"||id=="B23"?.004f:.0008f;
        static float DefaultSmooth(string id)=>id=="B24"?.82f:id=="B16"?.48f:id=="B13"?.35f:id=="B10"?.08f:.25f;
        static void Configure(ModifierDefinition m)
        {
            m.operation=ModifierOperation.Component;m.defaultMask=MaskKind.FineMixture;m.requiresReference=false;
            switch(m.stableId)
            {
                case "tint":m.operation=ModifierOperation.Palette;break;
                case "grain_size":m.operation=ModifierOperation.GrainScale;break;
                case "consolidation":m.operation=ModifierOperation.Consolidation;break;
                case "lamination":m.operation=ModifierOperation.Bedding;break;
                case "cleavage":m.operation=ModifierOperation.Cleavage;break;
                case "mud_supported":case "grain_supported":m.operation=ModifierOperation.SupportFabric;break;
                case "coal_lignite_variant":m.operation=ModifierOperation.Palette;break;
                case "surface_sheen":m.operation=ModifierOperation.Sheen;break;
                case "banding":m.operation=ModifierOperation.Banding;break;
                case "welding":m.operation=ModifierOperation.Welding;break;
                case "poor_sorting":m.operation=ModifierOperation.Sorting;break;
                case "recrystallization":m.operation=ModifierOperation.Recrystallization;break;
                case "igneous_mineral_balance":m.operation=ModifierOperation.MineralBalance;break;
                case "plasticity":case "alteration_unspecified":m.operation=ModifierOperation.Metadata;break;
                case "dike_geometry":m.operation=ModifierOperation.Geometry;break;
                case "contamination_status":m.operation=ModifierOperation.Status;break;
                case "bound_framework":case "cement_unspecified":case "serpentine_component":case "carbonate_component":case "iron_mineral_component":case "phosphate_component":case "paleosol_features":m.operation=ModifierOperation.Conditional;m.requiresReference=true;break;
                case "rounded_clasts":m.defaultMask=MaskKind.RoundedClast;break;
                case "angular_clasts":case "lithic_fragments":case "granitic_clasts":m.defaultMask=MaskKind.AngularClast;break;
                case "shell_fragments":m.defaultMask=MaskKind.Shell;break;
                case "ooids":m.defaultMask=MaskKind.Ooid;break;
                case "sand_content":case "silt_content":case "carbonate_grains":case "glauconite_component":m.defaultMask=MaskKind.Grain;break;
                case "crystal_fragments":case "pyrite_component":m.defaultMask=MaskKind.Crystal;break;
                case "glass_shards":m.defaultMask=MaskKind.Glass;break;
                case "mica_flakes":case "muscovite_component":m.defaultMask=MaskKind.Flake;break;
                case "vesicles":m.defaultMask=MaskKind.Pore;break;
                case "biotitization":case "chloritization":case "sericitization":case "silicification":m.requiresReference=true;m.defaultMask=MaskKind.Patches;break;
            }
        }
        [MenuItem("Tools/Lithology/Generate Preview Materials",priority=25)]
        public static void GenerateMaterials()
        {
            var c=Catalog;if(c==null)throw new InvalidOperationException("Initialize catalogue first");
            using(var cache=new LithologyMaterialCache())foreach(var d in c.definitions)foreach(DisplayMode mode in Enum.GetValues(typeof(DisplayMode)))
            {
                using(var lease=cache.Acquire(c,d,mode))
                {
                    string path=Output+"/Materials/"+d.stableId+"-"+mode+".mat";Directory.CreateDirectory(Path.GetDirectoryName(path));
                    var existing=AssetDatabase.LoadAssetAtPath<Material>(path);
                    if(existing==null) {existing=new Material(lease.Material){hideFlags=HideFlags.None};AssetDatabase.CreateAsset(existing,path);}
                    else EditorUtility.CopySerialized(lease.Material,existing);
                    existing.hideFlags=HideFlags.None;EditorUtility.SetDirty(existing);
                }
            }
            AssetDatabase.SaveAssets();
        }
        static void WriteProcurement(LithologyCatalog c)
        {
            var lines=new List<string>{"ID\tFamily\tSearch and asset requirements\tMaps\tAssumed macro tile (metres)\tAssigned labels\tSource URL\tLicense\tScale evidence\tAsset status"};
            foreach(var b in c.bases) lines.Add(string.Join("\t",b.stableId,b.description,b.procurementGuidance,"albedo sRGB; Y+ normal; roughness or smoothness linear; optional AO/height/masks",b.macroTileMetres.ToString(System.Globalization.CultureInfo.InvariantCulture),string.Join(", ",c.definitions.Where(d=>d.generatedRecipe.baseTexture==b).Select(d=>d.rawLabel)),b.sourceUrl,b.licenseNotes,b.physicalScaleEvidence,b.conditional?"Conditional, inactive":b.illustrativePreview?"Illustrative preview":"User-supplied reference"));
            File.WriteAllLines(Root+"/Documentation/TextureProcurement.tsv",lines);
        }
    }
    [InitializeOnLoad] static class LithologyEditorLifetime
    {
        static LithologyEditorLifetime()
        {
            AssemblyReloadEvents.beforeAssemblyReload+=LithologyMaterialCache.ResetShared;
            EditorApplication.quitting+=LithologyMaterialCache.ResetShared;
            EditorApplication.playModeStateChanged+=state=>
            {
                if(state==PlayModeStateChange.ExitingEditMode||state==PlayModeStateChange.ExitingPlayMode)LithologyMaterialCache.ResetShared();
                if(state==PlayModeStateChange.EnteredEditMode||state==PlayModeStateChange.EnteredPlayMode)
                    foreach(var r in UnityEngine.Object.FindObjectsByType<LithologyRenderer>(FindObjectsSortMode.None))r.Apply();
            };
        }
    }
}
