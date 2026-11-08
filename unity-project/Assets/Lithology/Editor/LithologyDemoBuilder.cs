using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace Subsurface.Lithology.Editor
{
    public static class LithologyDemoBuilder
    {
        public const string ScenePath=LithologyBuilder.Output+"/Demo/LithologyDemo.unity";
        static LithologyCatalog c;
        static Font font;
        [MenuItem("Tools/Lithology/Create Demo Scene",priority=40)]
        public static void Create()
        {
            c=LithologyBuilder.Catalog;if(c==null)throw new System.InvalidOperationException("Initialize catalogue first");
            var loaded=SceneManager.GetSceneByPath(ScenePath);
            if(loaded.IsValid()&&loaded.isLoaded){SceneManager.SetActiveScene(loaded);return;}
            if(File.Exists(ScenePath)){EditorSceneManager.OpenScene(ScenePath,OpenSceneMode.Additive);return;}
            // Additive creation leaves all existing and unsaved scene work intact.
            var previous=SceneManager.GetActiveScene();var scene=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Additive);SceneManager.SetActiveScene(scene);
            font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            var camera=new GameObject("Lithology camera").AddComponent<Camera>();camera.tag="MainCamera";camera.orthographic=true;camera.orthographicSize=10.8f;camera.transform.position=new Vector3(11,17,-15);camera.transform.LookAt(new Vector3(0,0,4));camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.075f,.10f,.115f);camera.rect=new Rect(0,0,.75f,1);camera.farClipPlane=180;camera.nearClipPlane=.1f;camera.gameObject.AddComponent<UniversalAdditionalCameraData>();
            var light=new GameObject("Key light").AddComponent<Light>();light.type=LightType.Directional;light.intensity=2.1f;light.shadows=LightShadows.Soft;light.transform.rotation=Quaternion.Euler(42,-28,0);
            var fill=new GameObject("Fill light").AddComponent<Light>();fill.type=LightType.Directional;fill.intensity=.65f;fill.color=new Color(.65f,.77f,1);fill.transform.rotation=Quaternion.Euler(35,145,0);
            RenderSettings.ambientMode=AmbientMode.Trilight;RenderSettings.ambientSkyColor=new Color(.38f,.43f,.44f);RenderSettings.ambientEquatorColor=new Color(.23f,.25f,.25f);RenderSettings.ambientGroundColor=new Color(.12f,.12f,.1f);
            for(int i=0;i<25;i++)
            {
                var b=c.bases[i];var d=c.definitions.FirstOrDefault(v=>v.generatedRecipe.baseTexture==b);
                Vector3 pos=new Vector3((i%5-2)*2.6f,.7f,(i/5)*2.6f);
                if(d!=null)Block(b.stableId,pos,new Vector3(1.8f,1.4f,1.8f),d);
                else {var marker=Block("B25 conditional",pos,new Vector3(1.8f,1.4f,1.8f),c.Resolve("Serpentine").definition);}
                Label(b.stableId+"  "+ShortName(b.description)+(b.conditional?"\nCONDITIONAL":""),pos+new Vector3(0,-.65f,-1.2f),.14f);
            }
            Label("25 SHARED FAMILIES  /  ILLUSTRATIVE PREVIEWS",new Vector3(0,.1f,12.4f),.24f);
            string[] recipes={"Fine Sandstone","Coarse Sandstone","Calcareous Sandstone","Shaly Sandstone","Shale Green","Slaty Shale","Argillaceous Limestone","Sandy and Calcareous Dolomite","Marl","Till","Coquina","Wackstone","Packstone","Grainstone","Lithic Tuff","Welded Tuff","Glassy Tuff","Chert - Tiger Stripe","Obsidian"};
            for(int i=0;i<recipes.Length;i++)
            {
                var pos=new Vector3(32+(i%5-2)*2.7f,.7f,(i/5)*2.7f);var r=Block(recipes[i],pos,new Vector3(1.9f,1.4f,1.9f),c.Resolve(recipes[i]).definition);
                Label(recipes[i].Replace(" and "," + ").Replace("Calcareous","Calc."),pos+new Vector3(0,-.65f,-1.2f),.13f);
            }
            var pyrite=Block("Pyrite on explicit sandstone preview host",new Vector3(37.4f,.7f,8.1f),new Vector3(1.9f,1.4f,1.9f),c.Resolve("Sandstone").definition);
            pyrite.hostModifiers.Add(new ModifierSettings {id="pyrite_component",strength=.55f,occurrence=MaskKind.Crystal});pyrite.Apply();Label("Pyrite / explicit preview host",new Vector3(37.4f,.05f,6.9f),.13f);
            string[] states={"Unknown","Not Available","No Sample","Others","Sandy","Clay-S"};
            for(int i=0;i<states.Length;i++){var p=new Vector3(58+i*2.3f,.5f,8);Block(states[i],p,new Vector3(1.6f,1,1.6f),c.Resolve(states[i]).definition);Label(states[i],p+new Vector3(0,-.45f,-1.1f),.13f);}
            for(int y=0;y<4;y++)
            {
                string label=new[]{"Shale","Fine Sandstone","Limestone","Marl"}[y];
                // Solid stepped cuboids provide real exposed caps and side faces.
                Block("Cut layer "+y,new Vector3(63,y*.7f,1+y*.32f),new Vector3(6,.7f,4-y*.65f),c.Resolve(label).definition);
                Label(label,new Vector3(59.7f,y*.7f,0),.16f);
            }
            for(int i=0;i<2;i++)Block("Adjacent aligned bed "+i,new Vector3(61+i*2.01f,.7f,5),new Vector3(2,1.4f,1.6f),c.Resolve("Slaty Shale").definition);
            var movable=Block("Movable object-rest specimen",new Vector3(68,1.4f,2),new Vector3(1.7f,2.2f,1.4f),c.Resolve("Chert - Tiger Stripe").definition);
            movable.visual.overrideCoordinates=true;movable.visual.coordinates.space=ProjectionSpace.ObjectRest;movable.transform.rotation=Quaternion.Euler(0,15,8);movable.transform.localScale=new Vector3(-1.7f,2.2f,1.4f);movable.Apply();Label("Movable / object-rest\nnegative nonuniform scale",new Vector3(68,.1f,.2f),.13f);
            var specimen=Block("Selected identity specimen",new Vector3(0,1,-3.6f),new Vector3(.25f,.2f,.18f),c.Resolve("Fine Sandstone").definition);
            Label("SELECTED IDENTITY",new Vector3(0,.05f,-5),.19f);
            var panel=ScriptableObject.CreateInstance<PanelSettings>();panel.name="Lithology demo panel";panel.scaleMode=PanelScaleMode.ScaleWithScreenSize;panel.referenceResolution=new Vector2Int(1280,800);
            Directory.CreateDirectory(LithologyBuilder.Output+"/Demo");string panelPath=LithologyBuilder.Output+"/Demo/PanelSettings.asset";
            var existing=AssetDatabase.LoadAssetAtPath<PanelSettings>(panelPath);if(existing!=null){Object.DestroyImmediate(panel);panel=existing;}else AssetDatabase.CreateAsset(panel,panelPath);
            // Editor-generated theme asset, without referring to Unity's internal runtime theme path.
            string themePath=LithologyBuilder.Output+"/Demo/LithologyTheme.tss";
            if(!File.Exists(themePath)){File.WriteAllText(themePath,"@import url(\"unity-theme://default\");\n");AssetDatabase.ImportAsset(themePath);}
            panel.themeStyleSheet=AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>(themePath);EditorUtility.SetDirty(panel);
            var ui=new GameObject("Lithology controls");ui.AddComponent<UIDocument>().panelSettings=panel;var ctrl=ui.AddComponent<LithologyDemoController>();ctrl.catalog=c;ctrl.specimen=specimen;ctrl.viewCamera=camera;ctrl.movableSpecimen=movable.transform;ctrl.styleSheet=AssetDatabase.LoadAssetAtPath<StyleSheet>(LithologyBuilder.Root+"/Runtime/LithologyDemo.uss");ctrl.displays=Object.FindObjectsByType<LithologyRenderer>(FindObjectsSortMode.None).Where(r=>r.gameObject.scene==scene).ToArray();
            Directory.CreateDirectory(Path.GetDirectoryName(ScenePath));SaveDemo(scene);AssetDatabase.SaveAssets();
            Debug.Log("Lithology demo saved additively; original scene work preserved: "+previous.name);
        }
        public static void SaveDemo(Scene scene)
        {
            var renderers=Object.FindObjectsByType<LithologyRenderer>(FindObjectsSortMode.None).Where(r=>r.gameObject.scene==scene).ToArray();
            foreach(var r in renderers)if(r.definition!=null&&r.target!=null)
            {
                var material=AssetDatabase.LoadAssetAtPath<Material>(LithologyBuilder.Output+"/Materials/"+r.definition.stableId+"-"+r.displayMode+".mat");
                if(material!=null){var slots=r.target.sharedMaterials;slots[r.materialSlot]=material;r.target.sharedMaterials=slots;}
            }
            EditorSceneManager.SaveScene(scene,ScenePath);foreach(var r in renderers)r.Apply();
        }
        static LithologyRenderer Block(string name,Vector3 p,Vector3 scale,LithologyDefinition d)
        {
            var go=GameObject.CreatePrimitive(PrimitiveType.Cube);Object.DestroyImmediate(go.GetComponent<Collider>());go.name=name;go.transform.position=p;go.transform.localScale=scale;
            var r=go.AddComponent<LithologyRenderer>();r.catalog=c;r.definition=d;r.Apply();return r;
        }
        static string ShortName(string s){int idx=s.IndexOf('/');if(idx>0)s=s.Substring(0,idx);if(s.Length>23)s=s.Substring(0,23);return s.Trim();}
        static void Label(string text,Vector3 pos,float scale)
        {
            var go=new GameObject(text);go.transform.position=pos;go.transform.rotation=Quaternion.Euler(pos.x>55?48:68,0,0);var tm=go.AddComponent<TextMesh>();tm.text=WrapText(text,18);tm.font=font;tm.fontSize=48;tm.characterSize=scale*.42f;tm.anchor=TextAnchor.MiddleCenter;tm.alignment=TextAlignment.Center;tm.color=new Color(.84f,.88f,.81f);go.GetComponent<MeshRenderer>().sharedMaterial=font.material;
        }
        public static string WrapText(string text,int max)
        {
            var lines=new System.Collections.Generic.List<string>();foreach(string original in text.Split('\n')){string line="";foreach(string word in original.Split(' ')){if(line.Length+word.Length+1>max&&line.Length>0){lines.Add(line);line="";}line+=(line.Length>0?" ":"")+word;}lines.Add(line);}return string.Join("\n",lines);
        }
        [MenuItem("Tools/Lithology/Open Demo Scene",priority=41)] public static void Open()
        {
            var loaded=SceneManager.GetSceneByPath(ScenePath);if(loaded.IsValid()&&loaded.isLoaded){SceneManager.SetActiveScene(loaded);return;}
            EditorSceneManager.OpenScene(ScenePath,OpenSceneMode.Additive);
        }
    }
}
