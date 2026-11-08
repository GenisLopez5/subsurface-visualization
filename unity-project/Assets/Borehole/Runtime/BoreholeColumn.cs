using System.Collections.Generic;
using Subsurface.Lithology;
using UnityEngine;

namespace Subsurface.Borehole
{
    public sealed class BoreholeSection : MonoBehaviour { public int Index; }
    public sealed class BoreholeColumn : MonoBehaviour
    {
        public LithologyCatalog catalog;
        public Shader highlightShader;
        [Min(.1f)]public float radius=1.25f;
        [Min(1)]public float displayHeight=24;
        public int renderingLayer=29;
        public BoreholeDocument Document {get;private set;}
        public BoreholeDepthMap DepthMap {get;private set;}
        public readonly List<LithologyRenderer> Surfaces=new List<LithologyRenderer>();
        public readonly List<Color> IdentifierColors=new List<Color>();
        public int SelectedIndex {get;private set;}=-1;
        GameObject generation;
        Mesh cylinder;
        Material highlightMaterial;
        LineRenderer topHighlight,bottomHighlight;
        readonly List<LineRenderer> boundaries=new List<LineRenderer>();Material boundaryMaterial;
        public void Build(BoreholeDocument document)
        {
            if(catalog==null||catalog.shader==null)throw new System.InvalidOperationException("The lithology catalogue must be assigned.");
            var map=new BoreholeDepthMap(document.TopFeet,document.BottomFeet,displayHeight);
            if(!float.IsFinite(radius)||radius<=0)throw new System.ArgumentException("The schematic cylinder radius must be positive.");
            Clear();Document=document;DepthMap=map;
            if(cylinder==null)cylinder=CreateCylinder(64);
            generation=new GameObject("CSV intervals");generation.transform.SetParent(transform,false);generation.layer=renderingLayer;
            for(int i=0;i<document.Intervals.Count;i++)
            {
                var interval=document.Intervals[i];float top=DepthMap.ToY(interval.TopFeet),bottom=DepthMap.ToY(interval.BottomFeet);
                var go=new GameObject("Interval "+i+" / "+interval.Lithology);go.layer=renderingLayer;go.transform.SetParent(generation.transform,false);go.transform.localPosition=new Vector3(0,top,0);go.transform.localScale=new Vector3(radius,top-bottom,radius);
                go.AddComponent<MeshFilter>().sharedMesh=cylinder;var renderer=go.AddComponent<MeshRenderer>();renderer.sharedMaterials=new Material[1];go.AddComponent<MeshCollider>().sharedMesh=cylinder;go.AddComponent<BoreholeSection>().Index=i;
                var surface=go.AddComponent<LithologyRenderer>();surface.catalog=catalog;surface.target=renderer;
                surface.rawLabel=interval.IsGap?"Not Available":interval.Lithology;surface.Apply();Surfaces.Add(surface);
                var definition=catalog.Resolve(surface.rawLabel).definition;var recipe=LithologyRecipeCompiler.Compile(catalog,definition);IdentifierColors.Add(interval.IsGap?new Color(.47f,.56f,.63f):recipe.legendColor);
            }
            Shader unlit=highlightShader!=null?highlightShader:BoreholeRenderAssets.Load().selectionShader;
            if(unlit!=null){highlightMaterial=new Material(unlit){name="Borehole selection",hideFlags=HideFlags.HideAndDontSave};highlightMaterial.SetColor("_BaseColor",new Color(.47f,1,0));topHighlight=Ring("Selection top");bottomHighlight=Ring("Selection bottom");}
            if(unlit!=null)
            {
                boundaryMaterial=new Material(unlit){name="Interval boundaries",hideFlags=HideFlags.HideAndDontSave};boundaryMaterial.SetColor("_BaseColor",new Color(.12f,.10f,.14f));
                for(int i=0;i<document.Intervals.Count;i++)
                {
                    var line=Ring("Interval boundary "+i);line.sharedMaterial=boundaryMaterial;SetRing(line,DepthMap.ToY(document.Intervals[i].TopFeet));boundaries.Add(line);
                }
                var end=Ring("Final interval boundary");end.sharedMaterial=boundaryMaterial;SetRing(end,DepthMap.ToY(document.BottomFeet));boundaries.Add(end);
            }
            if(topHighlight!=null)topHighlight.enabled=false;if(bottomHighlight!=null)bottomHighlight.enabled=false;
            SelectedIndex=-1;
        }
        LineRenderer Ring(string name)
        {
            var go=new GameObject(name);go.transform.SetParent(generation.transform,false);go.layer=renderingLayer;var line=go.AddComponent<LineRenderer>();line.sharedMaterial=highlightMaterial;line.useWorldSpace=false;line.loop=true;line.positionCount=64;line.widthMultiplier=.025f;line.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;line.receiveShadows=false;return line;
        }
        public void Select(int index)
        {
            if(Document==null||index<0||index>=Document.Intervals.Count)return;SelectedIndex=index;
            var row=Document.Intervals[index];if(topHighlight!=null)topHighlight.enabled=true;if(bottomHighlight!=null)bottomHighlight.enabled=true;SetRing(topHighlight,DepthMap.ToY(row.TopFeet));SetRing(bottomHighlight,DepthMap.ToY(row.BottomFeet));
        }
        void SetRing(LineRenderer line,float y){if(line==null)return;for(int i=0;i<64;i++){float a=i*Mathf.PI*2/64;line.SetPosition(i,new Vector3(Mathf.Cos(a)*radius*1.015f,y,Mathf.Sin(a)*radius*1.015f));}}
        readonly Dictionary<Material,Material> displayMaterials=new Dictionary<Material,Material>();
        readonly Dictionary<Material,Material> displaySources=new Dictionary<Material,Material>();
        float textureDisplayScale=1;
        public void SetMode(DisplayMode mode)
        {
            RestoreDisplayMaterials();foreach(var surface in Surfaces)surface.SetDisplayMode(mode);SetTextureDisplayScale(textureDisplayScale);
        }
        public void SetTextureDisplayScale(float scale)
        {
            textureDisplayScale=scale;
            foreach(var surface in Surfaces)if(surface!=null&&surface.target!=null)
            {
                var current=surface.target.sharedMaterial;if(current==null)continue;
                var source=displaySources.TryGetValue(current,out var original)?original:current;
                surface.target.SetPropertyBlock(null);
                if(Mathf.Abs(scale-1)<.00001f){surface.target.sharedMaterial=source;continue;}
                if(!displayMaterials.TryGetValue(source,out var material))
                {
                    material=new Material(source){name=source.name+" / viewer detail",hideFlags=HideFlags.DontSave};displayMaterials.Add(source,material);displaySources.Add(material,source);
                }
                material.SetFloat("_DisplayScale",scale);surface.target.sharedMaterial=material;
            }
        }
        void RestoreDisplayMaterials(bool immediate=false)
        {
            foreach(var surface in Surfaces)if(surface!=null&&surface.target!=null&&surface.target.sharedMaterial!=null&&displaySources.TryGetValue(surface.target.sharedMaterial,out var source))surface.target.sharedMaterial=source;
            foreach(var material in displayMaterials.Values)DestroyOwned(material,immediate);displayMaterials.Clear();displaySources.Clear();
        }
        public void SetHighlightWidth(float width){
            for(int i=0;i<boundaries.Count;i++)
            {
                double thickness=i==0?Document.Intervals[0].ThicknessFeet:i==Document.Intervals.Count?Document.Intervals[i-1].ThicknessFeet:System.Math.Min(Document.Intervals[i-1].ThicknessFeet,Document.Intervals[i].ThicknessFeet);
                boundaries[i].widthMultiplier=Mathf.Min(width*.45f,(float)(thickness/Document.SpanFeet)*displayHeight*.18f);
            }
            if(topHighlight!=null)topHighlight.widthMultiplier=width;if(bottomHighlight!=null)bottomHighlight.widthMultiplier=width;}
        public int Pick(Ray ray)
        {
            if(Physics.Raycast(ray,out var hit,1000,1<<renderingLayer)){var section=hit.collider.GetComponent<BoreholeSection>();if(section!=null&&section.transform.IsChildOf(transform))return section.Index;}return -1;
        }
        void Clear(bool immediate=false)
        {
            RestoreDisplayMaterials(immediate);
            boundaries.Clear();if(boundaryMaterial!=null)DestroyOwned(boundaryMaterial,immediate);boundaryMaterial=null;
            foreach(var s in Surfaces)if(s!=null)s.enabled=false;Surfaces.Clear();IdentifierColors.Clear();
            if(generation!=null){generation.SetActive(false);DestroyOwned(generation,immediate);}generation=null;
            if(highlightMaterial!=null)DestroyOwned(highlightMaterial,immediate);highlightMaterial=null;topHighlight=null;bottomHighlight=null;
        }
        public void ReleaseRuntimeResources(bool immediate=false)
        {Clear(immediate);if(cylinder!=null)DestroyOwned(cylinder,immediate);cylinder=null;Document=null;DepthMap=null;SelectedIndex=-1;}
        void OnDestroy()=>ReleaseRuntimeResources();
        static void DestroyOwned(Object o,bool immediate=false){if(Application.isPlaying&&!immediate)Destroy(o);else DestroyImmediate(o);}
        public static Mesh CreateCylinder(int count)
        {
            count=Mathf.Clamp(count,12,128);var vertices=new List<Vector3>();var normals=new List<Vector3>();var uv=new List<Vector2>();var triangles=new List<int>();
            for(int i=0;i<=count;i++)
            {
                float a=i*Mathf.PI*2/count;var n=new Vector3(Mathf.Cos(a),0,Mathf.Sin(a));vertices.Add(n);vertices.Add(n+Vector3.down);normals.Add(n);normals.Add(n);uv.Add(new Vector2(i/(float)count,1));uv.Add(new Vector2(i/(float)count,0));
                if(i<count){int k=i*2;triangles.AddRange(new[]{k,k+2,k+1,k+2,k+3,k+1});}
            }
            for(int cap=0;cap<2;cap++)
            {
                int center=vertices.Count;var origin=Vector3.down*cap;var normal=cap==0?Vector3.up:Vector3.down;vertices.Add(origin);normals.Add(normal);uv.Add(new Vector2(.5f,.5f));
                for(int i=0;i<=count;i++){float a=i*Mathf.PI*2/count;var p=new Vector3(Mathf.Cos(a),0,Mathf.Sin(a));vertices.Add(origin+p);normals.Add(normal);uv.Add(new Vector2(p.x*.5f+.5f,p.z*.5f+.5f));if(i<count){int j=center+1+i;if(cap==0)triangles.AddRange(new[]{center,j+1,j});else triangles.AddRange(new[]{center,j,j+1});}}
            }
            var mesh=new Mesh{name="Shared capped borehole cylinder",hideFlags=HideFlags.DontSave};mesh.SetVertices(vertices);mesh.SetNormals(normals);mesh.SetUVs(0,uv);mesh.SetTriangles(triangles,0);mesh.RecalculateBounds();mesh.RecalculateTangents();return mesh;
        }
    }
}
