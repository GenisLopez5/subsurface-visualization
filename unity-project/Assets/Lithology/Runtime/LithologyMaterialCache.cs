using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;

namespace Subsurface.Lithology
{
    public sealed class LithologyMaterialCache : IDisposable
    {
        sealed class Entry { public Material material; public int users; public long age; }
        public sealed class Lease : IDisposable
        {
            internal LithologyMaterialCache owner; internal string key;
            public Material Material { get; internal set; }
            public CompiledRecipe Recipe { get; internal set; }
            public void Dispose() { if(owner!=null) { owner.Release(key); owner=null; } }
        }
        readonly Dictionary<string,Entry> entries=new Dictionary<string,Entry>();
        long age;
        public int DormantCapacity { get; set; } = 48;
        public int Count => entries.Count;
        public static LithologyMaterialCache Shared { get; private set; } = new LithologyMaterialCache();
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        public static void ResetShared() { Shared?.Dispose(); Shared=new LithologyMaterialCache(); }
        public Lease Acquire(LithologyCatalog catalog,LithologyDefinition definition,DisplayMode mode=DisplayMode.Texture,VisualOverrides visual=null,IList<ModifierSettings> extra=null)
        {
            if(catalog==null || catalog.shader==null) throw new ArgumentException("Explicit catalogue and lithology shader are required.");
            var r=LithologyRecipeCompiler.Compile(catalog,definition,visual,extra);
            string key=Fingerprint(catalog,definition,r,mode,visual,extra);
            if(!entries.TryGetValue(key,out var e)) { e=new Entry { material=Build(catalog.shader,r,mode),age=++age }; entries.Add(key,e); }
            e.users++;e.age=++age;
            return new Lease { owner=this,key=key,Material=e.material,Recipe=r };
        }
        public static string Fingerprint(LithologyCatalog c,LithologyDefinition d,CompiledRecipe r,DisplayMode mode,VisualOverrides v,IList<ModifierSettings> extra)
        {
            // Full canonical serialized identity avoids hash collision aliasing. Texture object references
            // and all set fields/revisions participate. No persistent ID uses a runtime hash.
            var b=new StringBuilder();b.Append(c.GetEntityId().ToString()).Append(':').Append(c.shader!=null?c.shader.GetEntityId().ToString():"none").Append(':').Append(c.revision).Append(':').Append((int)mode).Append(':');
            if(d!=null) b.Append(JsonUtility.ToJson(d));
            if(r.source!=null) b.Append(JsonUtility.ToJson(r.source));
            b.Append(JsonUtility.ToJson(v??new VisualOverrides()));
            if(r.texture!=null)b.Append(JsonUtility.ToJson(r.texture));
            foreach(var l in r.layers) { b.Append(JsonUtility.ToJson(l));if(l.textureSource!=null)b.Append(JsonUtility.ToJson(l.textureSource)); }
            if(extra!=null)foreach(var m in extra)b.Append(JsonUtility.ToJson(m));
            return b.ToString();
        }
        static Material Build(Shader shader,CompiledRecipe r,DisplayMode mode)
        {
            var m=new Material(shader) { name="Lithology / "+(r.source!=null?r.source.stableId:"Fallback"),hideFlags=HideFlags.HideAndDontSave };
            var s=r.texture;
            m.SetTexture("_BaseMap",s!=null&&s.albedo!=null?s.albedo:Texture2D.whiteTexture);
            m.SetTexture("_NormalMap",s!=null?s.normal:null);
            m.SetTexture("_SurfaceMap",s!=null&&s.surface!=null?s.surface:Texture2D.whiteTexture);
            m.SetTexture("_DetailMap",s!=null&&s.detailAlbedo!=null?s.detailAlbedo:Texture2D.grayTexture);
            m.SetTexture("_DetailNormal",s!=null?s.detailNormal:null);
            m.SetTexture("_MineralMask",s!=null&&s.detailMask!=null?s.detailMask:Texture2D.grayTexture);
            m.SetTexture("_CoverageMask",s!=null&&s.componentMask!=null?s.componentMask:Texture2D.whiteTexture);
            m.SetFloat("_HasCoverageMask",s!=null&&s.componentMask!=null?1:0);
            m.SetFloat("_MineralBalance",r.mineralBalance);
            m.SetFloat("_HasMineralRegions",s!=null&&(s.stableId=="B09"||s.stableId=="B14"||s.stableId=="B15"||s.stableId=="B20"||s.stableId=="B21"||s.stableId=="B22"||s.stableId=="B23")?1:0);
            m.SetColor("_Tint",r.tint);m.SetColor("_MineralLight",r.light);m.SetColor("_MineralDark",r.dark);m.SetColor("_LegendColor",r.legendColor);
            m.SetFloat("_MacroScale",s!=null?Mathf.Max(.00001f,s.macroTileMetres):1);
            float detailScale=r.grain*r.exaggeration*(s!=null?s.detailTileMetres/.01f:1);
            if(s!=null&&(s.stableId=="B06"||s.stableId=="B07"))detailScale=r.structure.clastSpacingMetres*7;
            m.SetFloat("_GrainScale",Mathf.Max(.00001f,detailScale));
            float minPixels=24,maxPixels=0,reliefGain=1.8f,contrast=1.65f,grainSize=3,fineSurface=r.schematic?.55f:0;
            switch(s!=null?s.stableId:"")
            {
                case "B01":minPixels=10;maxPixels=32;reliefGain=.7f;contrast=1.65f;grainSize=1.5f;fineSurface=.65f;break;
                case "B02":minPixels=12;maxPixels=40;reliefGain=.7f;contrast=1.6f;grainSize=1.5f;fineSurface=.45f;break;
                case "B03":minPixels=10;maxPixels=32;reliefGain=.85f;contrast=2.2f;grainSize=1.5f;fineSurface=.8f;break;
                case "B04":minPixels=8;maxPixels=28;reliefGain=.42f;contrast=1.25f;grainSize=1.25f;break;
                case "B05":minPixels=12;maxPixels=48;reliefGain=.85f;contrast=1.4f;grainSize=1.75f;break;
            }
            m.SetFloat("_MinimumGrainPixels",minPixels);m.SetFloat("_MaximumGrainPixels",maxPixels);m.SetFloat("_ReliefGain",reliefGain);m.SetFloat("_RockContrast",contrast);m.SetFloat("_RockGrainSize",grainSize);m.SetFloat("_FineSurfaceStrength",fineSurface);
            m.SetFloat("_DetailStrength",s!=null&&s.independentGrainScale?s.detailColorStrength:0);
            m.SetFloat("_DetailEnabled",s!=null&&s.independentGrainScale?1:0);
            m.SetFloat("_NormalStrength",r.normal); m.SetFloat("_HasNormal",s!=null&&s.normal!=null?1:0);m.SetFloat("_HasDetailNormal",s!=null&&s.detailNormal!=null&&s.independentGrainScale?1:0);
            m.SetFloat("_InvertGreen",s!=null&&s.invertNormalGreen?1:0);m.SetFloat("_Smoothness",r.smoothness); m.SetFloat("_HasSurface",s!=null&&s.surface!=null?1:0);
            m.SetFloat("_WorldSpace",r.coordinates.space==ProjectionSpace.GeologicalWorld?1:0);m.SetFloat("_Triplanar",r.coordinates.triplanar?1:0);
            m.SetFloat("_UnitMetres",r.coordinates.metresPerUnityUnit);m.SetVector("_GeoOrigin",r.coordinates.geologicalOrigin);
            m.SetVector("_BeddingNormal",r.coordinates.beddingNormal);m.SetVector("_CleavageNormal",r.coordinates.cleavageNormal);
            var st=r.structure;
            m.SetVector("_Structure",new Vector4(st.bedding,st.cleavage,st.fractures,st.banding));
            m.SetVector("_Spacing",new Vector4(Mathf.Max(.00001f,st.beddingSpacingMetres),Mathf.Max(.00001f,st.cleavageSpacingMetres),Mathf.Max(.00001f,st.fractureSpacingMetres),Mathf.Max(.00001f,st.mottleSpacingMetres)));
            m.SetVector("_Fabric",new Vector4(st.welding,st.poorSorting,st.grainSupport,st.carbonateMudPresent?1:0));
            m.SetFloat("_VeinWidth",Mathf.Max(.00001f,st.veinWidthMetres));m.SetFloat("_Irregularity",st.irregularity);
            m.SetFloat("_Seed",r.seed%8191); m.SetFloat("_Legend",mode==DisplayMode.Legend?1:0);m.SetFloat("_Schematic",r.schematic?1:0);m.SetFloat("_StatusPattern",r.statusPattern);
            m.SetFloat("_ProceduralDark",r.proceduralDark?1:0);
            m.SetFloat("_LayerCount",r.valid?r.layers.Count:0);
            for(int i=0;i<2;i++)
            {
                var l=r.valid&&i<r.layers.Count?r.layers[i]:new ComponentLayer { coverage=0 };
                string suffix=(i+1).ToString();
                m.SetColor("_LayerColor"+suffix,l.color);m.SetVector("_Layer"+suffix,new Vector4((int)l.mask,l.coverage,Mathf.Max(.00001f,l.spacingMetres*r.exaggeration),l.relief));
                m.SetVector("_LayerSurface"+suffix,new Vector4(l.smoothness,l.metallic,l.textureSource!=null?1:0,0));
                var t=l.textureSource;
                m.SetTexture("_ComponentMap"+suffix,t!=null&&t.albedo!=null?t.albedo:Texture2D.whiteTexture);
                m.SetTexture("_ComponentNormal"+suffix,t!=null?t.normal:null);
                m.SetTexture("_ComponentSurface"+suffix,t!=null&&t.surface!=null?t.surface:Texture2D.whiteTexture);
            }
            return m;
        }
        void Release(string key) { if(entries.TryGetValue(key,out var e)) { e.users=Mathf.Max(0,e.users-1);e.age=++age;Trim(); } }
        void Trim()
        {
            while(true) { int dormant=0;string oldest=null;long oldestAge=long.MaxValue;
                foreach(var kv in entries) if(kv.Value.users==0) { dormant++;if(kv.Value.age<oldestAge) { oldest=kv.Key;oldestAge=kv.Value.age; } }
                if(dormant<=DormantCapacity||oldest==null)break;Destroy(entries[oldest].material);entries.Remove(oldest);
            }
        }
        static void Destroy(Material m) { if(m==null)return;if(Application.isPlaying)UnityEngine.Object.Destroy(m);else UnityEngine.Object.DestroyImmediate(m); }
        public void Dispose() { foreach(var e in entries.Values)Destroy(e.material);entries.Clear(); }
    }
}
