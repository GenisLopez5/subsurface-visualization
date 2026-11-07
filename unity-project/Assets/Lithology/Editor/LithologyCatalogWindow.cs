using System;
using System.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace Subsurface.Lithology.Editor
{
    public class LithologyCatalogWindow : EditorWindow
    {
        TextField search;
        DropdownField family;
        ScrollView list,details;
        LithologyDefinition selected;
        UnityEditor.Editor materialEditor;
        LithologyMaterialCache.Lease preview;
        [MenuItem("Tools/Lithology/Catalogue and Texture Authoring",priority=5)]
        public static void Open()=>GetWindow<LithologyCatalogWindow>("Lithology catalogue");
        public void CreateGUI()
        {
            rootVisualElement.Clear();
            var toolbar=new Toolbar();toolbar.Add(new ToolbarButton(()=>{LithologyBuilder.Initialize();Refresh();}){text="Initialize / Update"});toolbar.Add(new ToolbarButton(()=>LithologyValidation.Validate()){text="Validate"});rootVisualElement.Add(toolbar);
            var filters=new VisualElement();filters.style.flexDirection=FlexDirection.Row;
            search=new TextField("Label search");search.style.flexGrow=1;search.RegisterValueChangedCallback(_=>Refresh());filters.Add(search);
            family=new DropdownField("Base family",new[]{"All","No selected base"}.Concat(Enumerable.Range(1,25).Select(i=>"B"+i.ToString("D2"))).ToList(),0);family.RegisterValueChangedCallback(_=>Refresh());filters.Add(family);rootVisualElement.Add(filters);
            var body=new VisualElement();body.style.flexDirection=FlexDirection.Row;body.style.flexGrow=1;
            list=new ScrollView();list.style.width=290;details=new ScrollView();details.style.flexGrow=1;body.Add(list);body.Add(details);rootVisualElement.Add(body);Refresh();
        }
        void Refresh()
        {
            list.Clear();var c=LithologyBuilder.Catalog;if(c==null){list.Add(new Label("Initialize the catalogue first."));return;}
            foreach(var d in c.definitions.Where(d=>d.rawLabel.IndexOf(search.value??"",StringComparison.OrdinalIgnoreCase)>=0))
            {
                var b=d.EffectiveRecipe.baseTexture;
                if(family.value=="No selected base"&&b!=null)continue;if(family.value.StartsWith("B")&&(b==null||b.stableId!=family.value))continue;
                var button=new Button(()=>Show(d)){text=d.stableId+"  "+d.rawLabel+"\n"+(b!=null?b.stableId:d.role.ToString())+" · "+d.interpretation};button.style.whiteSpace=WhiteSpace.Normal;button.style.minHeight=40;list.Add(button);
            }
        }
        void Show(LithologyDefinition d)
        {
            selected=d;details.Clear();DisposePreview();
            var heading=new Label(d.rawLabel+"  ["+d.stableId+"]");heading.style.fontSize=22;heading.style.marginBottom=10;details.Add(heading);
            AddText(d.role+" / "+d.interpretation+"\n"+d.sourceInstructions+"\nProvisional name: "+d.provisionalNormalizedName+" (not an approved alias)");
            var so=new SerializedObject(d);var field=new PropertyField(so.FindProperty("confirmedRecipe"),"Confirmed recipe override");field.Bind(so);details.Add(field);
            details.Add(new Button(()=>CreateOverride(d)){text="Create editable override from generated defaults"});
            AddText("The generated recipe is rebuilt from the seed. Edit a confirmed override to preserve authored changes. Source verification remains a separate evidence decision.");
            var recipe=d.EffectiveRecipe;var recipeObject=new SerializedObject(recipe);
            foreach(string name in new[]{"baseTexture","palette","mineralLight","mineralDark","mineralDarkCoverage","grainMetres","schematicExaggeration","normalStrength","smoothness","coordinates","structure","componentLayers","modifiers","seed","revision"})
            {
                var f=new PropertyField(recipeObject.FindProperty(name));f.SetEnabled(d.confirmedRecipe!=null);f.Bind(recipeObject);details.Add(f);
            }
            var b=recipe.baseTexture;
            if(b!=null)
            {
                AddText(b.stableId+" · "+b.description+"\n"+b.sourceNotes+"\n"+b.procurementGuidance);
                var bs=new SerializedObject(b);
                foreach(string name in new[]{"albedo","normal","surface","detailAlbedo","detailNormal","detailMask","height","componentMask","macroTileMetres","detailTileMetres","independentGrainScale","detailColorStrength","invertNormalGreen","illustrativePreview","sourceUrl","licenseNotes","physicalScaleEvidence","revision"})
                {var f=new PropertyField(bs.FindProperty(name));f.Bind(bs);details.Add(f);}
                details.Add(new Button(()=>{LithologyTextureTools.ConfigureImports(b);b.revision++;EditorUtility.SetDirty(b);AssetDatabase.SaveAssets();Show(d);}){text="Apply map import conventions / refresh preview"});
            }
            var source=new PropertyField(so.FindProperty("sourceVerified"));source.Bind(so);details.Add(source);
            var evidence=new PropertyField(so.FindProperty("confirmationEvidence"));evidence.Bind(so);details.Add(evidence);
            details.Add(new Button(()=>ApplySelected(d)){text="Apply identity to selected Renderers (slot 0)"});
            var compiled=LithologyRecipeCompiler.Compile(LithologyBuilder.Catalog,d);AddText(string.Join("\n",compiled.diagnostics));
            if(compiled.layers.Count>2)AddText("AUTHORING ROUTE: keep this full recipe as provenance. Create a new BaseTextureSet, assign externally baked color / normal / packed maps containing the excess components, then use a confirmed recipe override referencing that set with no more than two live component layers. Disable already baked modifier treatments in the override. The source recipe remains intact. This tool never silently drops a layer.");
            preview=LithologyMaterialCache.Shared.Acquire(LithologyBuilder.Catalog,d);materialEditor=UnityEditor.Editor.CreateEditor(preview.Material);
            var image=new IMGUIContainer(()=>{if(materialEditor!=null)materialEditor.OnInteractivePreviewGUI(GUILayoutUtility.GetRect(240,240),EditorStyles.helpBox);});details.Add(image);
            details.Add(new Button(()=>Show(d)){text="Refresh material preview"});
        }
        void AddText(string s){var label=new Label(s);label.style.whiteSpace=WhiteSpace.Normal;label.style.marginTop=8;label.style.marginBottom=8;details.Add(label);}
        static void CreateOverride(LithologyDefinition d)
        {
            if(d.confirmedRecipe!=null){Selection.activeObject=d.confirmedRecipe;return;}
            string path=EditorUtility.SaveFilePanelInProject("Save authored recipe",d.stableId+"-Authored","asset","Create a separate recipe; generated defaults are preserved",LithologyBuilder.Root);
            if(string.IsNullOrEmpty(path))return;var recipe=Instantiate(d.generatedRecipe);recipe.stableId=d.stableId+"-authored";AssetDatabase.CreateAsset(recipe,path);Undo.RecordObject(d,"Assign authored interpretation");d.confirmedRecipe=recipe;EditorUtility.SetDirty(d);AssetDatabase.SaveAssets();GetWindow<LithologyCatalogWindow>().Show(d);
        }
        static void ApplySelected(LithologyDefinition d)
        {
            foreach(var go in Selection.gameObjects)foreach(var renderer in go.GetComponentsInChildren<Renderer>())
            {
                var r=renderer.GetComponent<LithologyRenderer>();if(r==null)r=Undo.AddComponent<LithologyRenderer>(renderer.gameObject);
                Undo.RecordObject(r,"Apply lithology");Undo.RecordObject(renderer,"Apply material slot");r.catalog=LithologyBuilder.Catalog;r.target=renderer;r.materialSlot=0;r.definition=d;r.Apply();EditorUtility.SetDirty(r);
            }
        }
        void DisposePreview(){if(materialEditor!=null)DestroyImmediate(materialEditor);materialEditor=null;preview?.Dispose();preview=null;}
        void OnDisable()=>DisposePreview();
    }
    public static class LithologyTextureTools
    {
        static void Import(Texture2D t,bool srgb,bool normal)
        {
            if(t==null)return;string path=AssetDatabase.GetAssetPath(t);var i=AssetImporter.GetAtPath(path) as TextureImporter;if(i==null)return;
            i.textureType=normal?TextureImporterType.NormalMap:TextureImporterType.Default;i.sRGBTexture=srgb&&!normal;i.mipmapEnabled=true;i.wrapMode=TextureWrapMode.Repeat;i.filterMode=FilterMode.Trilinear;i.SaveAndReimport();
        }
        public static void ConfigureImports(BaseTextureSet b)
        {
            Import(b.albedo,true,false);Import(b.detailAlbedo,true,false);Import(b.normal,false,true);Import(b.detailNormal,false,true);
            Import(b.surface,false,false);Import(b.height,false,false);Import(b.componentMask,false,false);Import(b.detailMask,false,false);
        }
        [MenuItem("Tools/Lithology/Pack Selected Roughness to Surface Map",priority=30)]
        public static void PackRoughness()
        {
            var t=Selection.activeObject as Texture2D;if(t==null)throw new InvalidOperationException("Select a roughness texture");
            string path=EditorUtility.SaveFilePanelInProject("Packed linear surface map",t.name+"-Surface","png","R=0 metallic G=1 AO B=0 A=1-roughness");if(string.IsNullOrEmpty(path))return;
            var old=RenderTexture.active;var rt=RenderTexture.GetTemporary(t.width,t.height,0,RenderTextureFormat.ARGB32,RenderTextureReadWrite.Linear);
            try {Graphics.Blit(t,rt);RenderTexture.active=rt;var read=new Texture2D(t.width,t.height,TextureFormat.RGBA32,false,true);read.ReadPixels(new Rect(0,0,t.width,t.height),0,0);read.Apply();var pixels=read.GetPixels();for(int k=0;k<pixels.Length;k++)pixels[k]=new Color(0,1,0,1-pixels[k].r);read.SetPixels(pixels);read.Apply();System.IO.File.WriteAllBytes(path,read.EncodeToPNG());UnityEngine.Object.DestroyImmediate(read);AssetDatabase.ImportAsset(path);Import(AssetDatabase.LoadAssetAtPath<Texture2D>(path),false,false);}
            finally{RenderTexture.active=old;RenderTexture.ReleaseTemporary(rt);}
        }
    }
}
