using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using Subsurface.Lithology.Editor;
using UnityEditor;
using UnityEngine;

namespace Subsurface.Lithology.Tests
{
    public class LithologyTests
    {
        LithologyCatalog c;
        [SetUp]public void Setup(){c=LithologyBuilder.Catalog;Assert.That(c,Is.Not.Null,"Run Tools/Lithology/Bootstrap Complete System first");c.RebuildLookups();}
        [Test]public void CompleteCataloguePreservesSourceOccurrences()
        {
            Assert.That(LithologyValidation.Check(c),Is.Empty);
            Assert.That(c.Resolve("Argillaceous Limesto").definition.sourceLocations.Select(s=>s.extractionLocation),Is.EquivalentTo(new[]{"section 1/Shale","section 2/Limestone"}));
            Assert.That(c.Resolve("Dolomitic").definition.sourceLocations.Select(s=>s.extractionLocation),Is.EquivalentTo(new[]{"section 2/Dolomite","section 5/Textural/Compositional"}));
            Assert.That(c.definitions.Count(d=>d.prefixExpanded),Is.EqualTo(4));
        }
        [TestCase("Fine Sandstone",ResolutionStatus.Exact)]
        [TestCase("  fine   sandstone  ",ResolutionStatus.Normalized)]
        [TestCase("not_a_known_rock",ResolutionStatus.Unknown)]
        [TestCase(null,ResolutionStatus.Invalid)]
        [TestCase("  ",ResolutionStatus.Invalid)]
        public void ResolverPreservesOriginal(string input,ResolutionStatus status)
        {var r=c.Resolve(input);Assert.That(r.status,Is.EqualTo(status));Assert.That(r.originalInput,Is.EqualTo(input));}
        [Test]public void ProvisionalCandidatesAreNotAliases()
        {
            Assert.That(c.Resolve("Tuffaceous Claystone").status,Is.EqualTo(ResolutionStatus.Unknown));
            Assert.That(c.Resolve("Wackestone").status,Is.EqualTo(ResolutionStatus.Unknown));
        }
        [Test]public void AliasesRequireEvidenceAndCollisionsAreAmbiguous()
        {
            var local=Object.Instantiate(c);
            try {
                local.verifiedAliases=new List<VerifiedAlias>{new VerifiedAlias{label="test alias",target=c.Resolve("Clay").definition,verificationEvidence="test-only evidence"}};local.RebuildLookups();Assert.That(local.Resolve("Test Alias").status,Is.EqualTo(ResolutionStatus.VerifiedAlias));
                local.verifiedAliases.Add(new VerifiedAlias{label="TEST ALIAS",target=c.Resolve("Sand").definition,verificationEvidence="test-only evidence"});local.RebuildLookups();Assert.That(local.Resolve("test alias").status,Is.EqualTo(ResolutionStatus.Ambiguous));
                local.verifiedAliases.Add(new VerifiedAlias{label="unevidenced",target=c.Resolve("Sand").definition});local.RebuildLookups();Assert.That(local.Resolve("unevidenced").status,Is.EqualTo(ResolutionStatus.Unknown));
            } finally {Object.DestroyImmediate(local);}
        }
        [Test]public void OpaqueCodesAndConditionalSourcesStayUnresolved()
        {
            foreach(var d in c.definitions.Where(d=>d.role==RenderRole.SourceCode)){Assert.That(d.generatedRecipe.baseTexture,Is.Null);Assert.That(d.requiresSourceLegend,Is.True);Assert.That(d.provisionalNormalizedName,Is.Empty);}
            foreach(string label in new[]{"Serpentine","Boundstone","Carbonates","Cement","Evaporites"})Assert.That(c.Resolve(label).definition.generatedRecipe.baseTexture,Is.Null);
        }
        [TestCase("Sandy and Calcareous Dolomite","sand_content","calcareous_component")]
        [TestCase("Argil and Sandy Dolomite","clay_content","sand_content")]
        public void CombinedComponentsSurvive(string label,string one,string two)
        {var r=LithologyRecipeCompiler.Compile(c,c.Resolve(label).definition);Assert.That(r.valid,Is.True);Assert.That(r.layers.Select(l=>l.semanticId),Is.EquivalentTo(new[]{one,two}));}
        [Test]public void OptionalShalyLaminationIsNotInferred()
        {
            var d=c.Resolve("Shaly Sandstone").definition;
            Assert.That(LithologyRecipeCompiler.Compile(c,d).structure.bedding,Is.Zero);
            Assert.That(LithologyRecipeCompiler.Compile(c,d,new VisualOverrides{optionalLamination=true}).structure.bedding,Is.GreaterThan(0));
            Assert.That(LithologyRecipeCompiler.Compile(c,c.Resolve("Plastic Clay").definition).smoothness,Is.EqualTo(d.generatedRecipe.smoothness));
        }
        [Test]public void ExtraLayersAndUnknownModifiersCannotDisappear()
        {
            var extras=new List<ModifierSettings>{new ModifierSettings{id="clay_content"},new ModifierSettings{id="sand_content"},new ModifierSettings{id="pyrite_component"}};
            var r=LithologyRecipeCompiler.Compile(c,c.Resolve("Sandstone").definition,null,extras);Assert.That(r.layers.Count,Is.EqualTo(3));Assert.That(r.valid,Is.False);Assert.That(r.schematic,Is.True);
            r=LithologyRecipeCompiler.Compile(c,c.Resolve("Sandstone").definition,null,new[]{new ModifierSettings{id="made_up"}});Assert.That(r.valid,Is.False);Assert.That(string.Join(" ",r.diagnostics),Does.Contain("made_up"));
        }
        [Test]public void UnknownHostNeverBecomesARock()
        {var r=LithologyRecipeCompiler.Compile(c,c.Resolve("Pyrite").definition);Assert.That(r.schematic,Is.True);Assert.That(r.layers,Is.Empty);}
        [Test]public void InvalidCoordinateScaleProducesFiniteSchematicFallback()
        {
            var r=LithologyRecipeCompiler.Compile(c,c.Resolve("Sandstone").definition,new VisualOverrides{overrideCoordinates=true,coordinates=new CoordinateSettings{metresPerUnityUnit=float.NaN,beddingNormal=Vector3.zero}});
            Assert.That(r.valid,Is.False);Assert.That(r.schematic,Is.True);Assert.That(float.IsNaN(r.coordinates.metresPerUnityUnit),Is.False);Assert.That(r.coordinates.beddingNormal.sqrMagnitude,Is.GreaterThan(.9f));
        }
        [Test]public void CacheSharesIdentityButSeparatesOverridesAndBoundsDormantMaterials()
        {
            using(var cache=new LithologyMaterialCache{DormantCapacity=4})
            {
                var d=c.Resolve("Sandstone").definition;
                using(var a=cache.Acquire(c,d))using(var b=cache.Acquire(c,d)){Assert.That(a.Material,Is.SameAs(b.Material));using(var different=cache.Acquire(c,d,DisplayMode.Legend))Assert.That(different.Material,Is.Not.SameAs(a.Material));}
                for(int i=0;i<30;i++)using(var lease=cache.Acquire(c,d,DisplayMode.Texture,new VisualOverrides{grainMultiplier=1+i*.1f})){}
                Assert.That(cache.Count,Is.LessThanOrEqualTo(4));
                var original=d.generatedRecipe.baseTexture;int rev=original.revision;
                try {using(var a=cache.Acquire(c,d)){original.revision++;using(var b=cache.Acquire(c,d))Assert.That(b.Material,Is.Not.SameAs(a.Material));}}finally{original.revision=rev;}
            }
        }
        [Test]public void GrainChangesDoNotResizeStructureAndZeroLayerRestoresHost()
        {
            using(var cache=new LithologyMaterialCache())
            {
                var d=c.Resolve("Slaty Shale").definition;
                using(var a=cache.Acquire(c,d))using(var b=cache.Acquire(c,d,DisplayMode.Texture,new VisualOverrides{grainMultiplier=3}))
                {Assert.That(a.Material.GetVector("_Spacing"),Is.EqualTo(b.Material.GetVector("_Spacing")));Assert.That(a.Material.GetFloat("_GrainScale"),Is.Not.EqualTo(b.Material.GetFloat("_GrainScale")));}
                using(var a=cache.Acquire(c,d))using(var b=cache.Acquire(c,d,DisplayMode.Texture,null,new[]{new ModifierSettings{id="pyrite_component",strength=0}}))
                {Assert.That(a.Material.GetFloat("_LayerCount"),Is.EqualTo(b.Material.GetFloat("_LayerCount")));Assert.That(a.Material.GetColor("_Tint"),Is.EqualTo(b.Material.GetColor("_Tint")));Assert.That(a.Material.GetFloat("_NormalStrength"),Is.EqualTo(b.Material.GetFloat("_NormalStrength")));}
            }
        }
        [Test]public void ApplyingOneSlotPreservesOtherSlotsAndRestoresOwnership()
        {
            var go=GameObject.CreatePrimitive(PrimitiveType.Cube);var other=GameObject.CreatePrimitive(PrimitiveType.Cube);
            try
            {
                var rend=go.GetComponent<Renderer>();var original=rend.sharedMaterial;rend.sharedMaterials=new[]{original,original};
                var r=go.AddComponent<LithologyRenderer>();r.catalog=c;r.target=rend;r.materialSlot=1;r.definition=c.Resolve("Shale").definition;r.Apply();
                Assert.That(rend.sharedMaterials[0],Is.SameAs(original));Assert.That(other.GetComponent<Renderer>().sharedMaterial,Is.SameAs(original));
                r.enabled=false;Assert.That(rend.sharedMaterials[1],Is.SameAs(original));
            } finally {Object.DestroyImmediate(go);Object.DestroyImmediate(other);}
        }
        [Test]public void InitializationPreservesGuidsTexturesAndAuthoredOverride()
        {
            var d=c.Resolve("Sandstone").definition;var b=d.generatedRecipe.baseTexture;string guid=AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(d));var texture=b.albedo;var original=d.confirmedRecipe;
            var authored=ScriptableObject.CreateInstance<LithologyMaterialRecipe>();authored.palette=Color.magenta;
            try {d.confirmedRecipe=authored;LithologyBuilder.Initialize();LithologyBuilder.Initialize();Assert.That(d.confirmedRecipe,Is.SameAs(authored));Assert.That(b.albedo,Is.SameAs(texture));Assert.That(AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(d)),Is.EqualTo(guid));Assert.That(c.definitions.Count,Is.EqualTo(180));}
            finally {d.confirmedRecipe=original;EditorUtility.SetDirty(d);Object.DestroyImmediate(authored);AssetDatabase.SaveAssets();}
        }
        [Test]public void PreviewEdgesTileAndMapsHaveCorrectImports()
        {
            foreach(var b in c.bases)
            {
                foreach(var t in new[]{b.albedo,b.detailAlbedo,b.detailMask,b.surface})
                {
                    Assert.That(t,Is.Not.Null,b.stableId);var p=t.GetPixels();int n=t.width;
                    for(int i=0;i<n;i++){Assert.That(Vector4.Distance(p[i*n],p[i*n+n-1]),Is.LessThan(.025f),t.name);Assert.That(Vector4.Distance(p[i],p[(n-1)*n+i]),Is.LessThan(.025f),t.name);}
                }
                Assert.That(((TextureImporter)AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(b.surface))).sRGBTexture,Is.False);
                Assert.That(((TextureImporter)AssetImporter.GetAtPath(AssetDatabase.GetAssetPath(b.detailNormal))).textureType,Is.EqualTo(TextureImporterType.NormalMap));
            }
        }
    }
}
