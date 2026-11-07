using System;
using System.IO;
using Subsurface.Lithology.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;

namespace Subsurface.Borehole.Editor
{
    [InitializeOnLoad]public static class BoreholeViewerBuilder
    {
        public const string Root="Assets/Borehole",ScenePath=Root+"/Generated/BoreholeViewer.unity";
        static BoreholeViewerBuilder(){DebugCsvFileDialog.EditorPicker=()=>EditorUtility.OpenFilePanel("Load lithology CSV","","csv");DebugCsvFileDialog.EditorImagePicker=()=>EditorUtility.OpenFilePanelWithFilters("Load segmented document","",new[]{"Document images","tif,tiff,png,jpg,jpeg"});}
        [MenuItem("Tools/Lithology/Open CSV Borehole Visualizer",priority=2)]
        public static void CreateOrOpen()
        {
            if(EditorApplication.isPlaying)throw new InvalidOperationException("Leave Play mode before opening a scene.");
            var existing=SceneManager.GetSceneByPath(ScenePath);if(existing.IsValid()&&existing.isLoaded){SceneManager.SetActiveScene(existing);return;}
            if(File.Exists(ScenePath)){EditorSceneManager.OpenScene(ScenePath,OpenSceneMode.Additive);return;}
            var catalog=LithologyBuilder.Catalog;if(catalog==null)throw new InvalidOperationException("Initialize the lithology catalogue first.");
            Directory.CreateDirectory(Root+"/Generated");AssetDatabase.Refresh();
            var scene=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Additive);SceneManager.SetActiveScene(scene);
            var column=new GameObject("Borehole column").AddComponent<BoreholeColumn>();column.catalog=catalog;column.highlightShader=Shader.Find("Universal Render Pipeline/Unlit");column.radius=1.25f;column.displayHeight=24;
            var camera=new GameObject("Borehole viewer camera").AddComponent<Camera>();camera.depth=-10;camera.orthographic=true;camera.orthographicSize=5;camera.nearClipPlane=.05f;camera.farClipPlane=120;camera.cullingMask=1<<column.renderingLayer;camera.backgroundColor=new Color(.063f,.094f,.114f);camera.clearFlags=CameraClearFlags.SolidColor;camera.gameObject.AddComponent<UniversalAdditionalCameraData>();
            var screen=new GameObject("Document screen camera").AddComponent<Camera>();screen.tag="MainCamera";screen.cullingMask=0;screen.depth=1;screen.backgroundColor=camera.backgroundColor;screen.clearFlags=CameraClearFlags.SolidColor;screen.gameObject.AddComponent<UniversalAdditionalCameraData>();
            camera.transform.position=new Vector3(12,1,-40);camera.transform.LookAt(Vector3.down*4);
            Light Light(string name,Vector3 rotation,float strength,Color color)
            {var light=new GameObject(name).AddComponent<Light>();light.type=LightType.Directional;light.transform.rotation=Quaternion.Euler(rotation);light.intensity=strength;light.color=color;light.cullingMask=1<<column.renderingLayer;return light;}
            Light("Borehole key",new Vector3(35,-35,0),1.8f,new Color(1,.94f,.83f)).shadows=LightShadows.Soft;
            Light("Borehole fill",new Vector3(25,135,0),.8f,new Color(.71f,.83f,1));
            RenderSettings.ambientMode=AmbientMode.Trilight;RenderSettings.ambientSkyColor=new Color(.38f,.43f,.45f);RenderSettings.ambientEquatorColor=new Color(.25f,.29f,.31f);RenderSettings.ambientGroundColor=new Color(.12f,.14f,.16f);
            string panelPath=Root+"/Generated/ViewerPanel.asset",themePath=Root+"/Generated/ViewerTheme.tss";
            if(!File.Exists(themePath)){File.WriteAllText(themePath,"@import url(\"unity-theme://default\");\n");AssetDatabase.ImportAsset(themePath);}
            var panel=AssetDatabase.LoadAssetAtPath<PanelSettings>(panelPath);
            if(panel==null){panel=ScriptableObject.CreateInstance<PanelSettings>();panel.name="Borehole viewer panel";panel.scaleMode=PanelScaleMode.ScaleWithScreenSize;panel.referenceResolution=new Vector2Int(1440,900);panel.themeStyleSheet=AssetDatabase.LoadAssetAtPath<ThemeStyleSheet>(themePath);AssetDatabase.CreateAsset(panel,panelPath);}
            var ui=new GameObject("Borehole document viewer");ui.SetActive(false);var document=ui.AddComponent<UIDocument>();document.panelSettings=panel;document.visualTreeAsset=AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Root+"/Runtime/BoreholeViewer.uxml");
            var viewer=ui.AddComponent<BoreholeViewer>();viewer.column=column;viewer.columnCamera=camera;viewer.presentationCamera=screen;viewer.initialCsv=AssetDatabase.LoadAssetAtPath<TextAsset>(Root+"/Samples/Allegheny.csv");viewer.initialDocumentImage=AssetDatabase.LoadAssetAtPath<Texture2D>(Root+"/Samples/Allegheny-preview.png");viewer.initialSourceName="Allegheny Attic · GC297 #3 · Mud log";viewer.isolateOtherScenes=true;camera.backgroundColor=new Color(.545f,.396f,.302f,1);ui.SetActive(true);
            EditorSceneManager.SaveScene(scene,ScenePath);AssetDatabase.SaveAssets();Selection.activeGameObject=ui;Debug.Log("Borehole viewer: scene ready. Press Play; use Load CSV (debug) to choose another document.");
        }
    }
}
