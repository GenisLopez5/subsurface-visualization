using System.Collections.Generic;
using UnityEngine;
namespace Subsurface.Lithology
{
    [ExecuteAlways,DisallowMultipleComponent]
    public class LithologyRenderer : MonoBehaviour
    {
        public LithologyCatalog catalog;
        public Renderer target;
        [Min(0)] public int materialSlot;
        public LithologyDefinition definition;
        public string rawLabel;
        public DisplayMode displayMode;
        public VisualOverrides visual = new VisualOverrides();
        public List<ModifierSettings> hostModifiers = new List<ModifierSettings>();
        [TextArea] public string diagnostics;
        LithologyMaterialCache.Lease lease;
        Renderer appliedTarget;
        int appliedSlot;
        Material previous;
        bool dirty;
        void OnEnable() => Apply();
        void OnValidate() { dirty=true; }
        void Update() { if(dirty) {dirty=false;Apply();} }
        public void SetLabel(string label) { rawLabel=label;definition=null;Apply(); }
        public void SetDefinition(LithologyDefinition value) { definition=value;rawLabel=value!=null?value.rawLabel:rawLabel;Apply(); }
        public void SetDisplayMode(DisplayMode mode) { displayMode=mode;Apply(); }
        public void SetOverrides(VisualOverrides value) { visual=value??new VisualOverrides();Apply(); }
        public void Apply()
        {
            if(catalog==null)return;
            if(target==null)target=GetComponent<Renderer>();
            if(target==null)return;
            var slots=target.sharedMaterials;
            if(materialSlot<0||materialSlot>=slots.Length){diagnostics="Material slot out of range; other slots preserved.";return;}
            var result=definition!=null?null:catalog.Resolve(rawLabel);
            var selected=definition!=null?definition:result.definition;
            if(lease!=null && (target!=appliedTarget||materialSlot!=appliedSlot)) Release();
            slots=target.sharedMaterials;
            var effective=visual;string projectionDiagnostic="";
            var coord=visual!=null&&visual.overrideCoordinates?visual.coordinates:selected!=null&&selected.EffectiveRecipe!=null?selected.EffectiveRecipe.coordinates:null;
            if(coord!=null&&!coord.triplanar)
            {
                var skinned=target as SkinnedMeshRenderer;var filter=target.GetComponent<MeshFilter>();var mesh=skinned!=null?skinned.sharedMesh:filter!=null?filter.sharedMesh:null;
                if(mesh==null||!mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.Tangent)||!mesh.HasVertexAttribute(UnityEngine.Rendering.VertexAttribute.TexCoord0))
                {
                    effective=JsonUtility.FromJson<VisualOverrides>(JsonUtility.ToJson(visual??new VisualOverrides()));effective.overrideCoordinates=true;effective.coordinates=JsonUtility.FromJson<CoordinateSettings>(JsonUtility.ToJson(coord));effective.coordinates.triplanar=true;
                    projectionDiagnostic="\nUV projection needs UV0 and tangents; this renderer uses triplanar fallback.";
                }
            }
            var next=LithologyMaterialCache.Shared.Acquire(catalog,selected,displayMode,effective,hostModifiers);
            if(lease==null) {previous=slots[materialSlot];appliedTarget=target;appliedSlot=materialSlot;}
            var old=lease;lease=next;slots[materialSlot]=next.Material;target.sharedMaterials=slots;old?.Dispose();
            diagnostics=(result!=null?result.diagnostic+" Input: "+rawLabel+"\n":"")+string.Join("\n",next.Recipe.diagnostics)+projectionDiagnostic;
            if(selected!=null&&selected.EffectiveRecipe!=null&&selected.EffectiveRecipe.baseTexture!=null&&!selected.EffectiveRecipe.baseTexture.independentGrainScale)
                diagnostics+="\nIndependent grain resizing disabled: assigned scan has inseparable macro/grain content.";
        }
        void OnDisable()=>Release();
        void OnDestroy()=>Release();
        void Release()
        {
            if(lease==null)return;
            if(appliedTarget!=null) {var slots=appliedTarget.sharedMaterials;if(appliedSlot<slots.Length&&slots[appliedSlot]==lease.Material) {slots[appliedSlot]=previous;appliedTarget.sharedMaterials=slots;}}
            lease.Dispose();lease=null;appliedTarget=null;previous=null;
        }
    }
}
