using System;
using Subsurface.Borehole;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;
namespace Subsurface.Explorer.Editor
{
    public static class ExplorerBuilder
    {
        public const string ScenePath="Assets/Explorer/DocumentExplorer.unity";
        [MenuItem("Tools/Lithology/Open Document Explorer",priority=1)]
        public static void Open()
        {
            if(EditorApplication.isPlaying)throw new InvalidOperationException("Leave Play mode before opening the explorer scene.");
            var existing=SceneManager.GetSceneByPath(ScenePath);if(existing.IsValid()&&existing.isLoaded){SceneManager.SetActiveScene(existing);return;}
            if(System.IO.File.Exists(ScenePath)){EditorSceneManager.OpenScene(ScenePath,OpenSceneMode.Single);return;}
            var scene=EditorSceneManager.OpenScene("Assets/Borehole/Generated/BoreholeViewer.unity",OpenSceneMode.Additive);
            SceneManager.SetActiveScene(scene);
            var viewer=Array.Find(UnityEngine.Object.FindObjectsByType<BoreholeViewer>(),x=>x.gameObject.scene==scene);
            viewer.initialCsv=null;viewer.initialDocumentImage=null;viewer.isolateOtherScenes=true;
            var go=new GameObject("Document explorer");SceneManager.MoveGameObjectToScene(go,scene);
            var ui=go.AddComponent<UIDocument>();ui.panelSettings=viewer.GetComponent<UIDocument>().panelSettings;ui.sortingOrder=10;
            var explorer=go.AddComponent<DocumentExplorer>();explorer.viewer=viewer;explorer.document=ui;explorer.stylesheet=AssetDatabase.LoadAssetAtPath<StyleSheet>("Assets/Explorer/Runtime/DocumentExplorer.uss");
            EditorSceneManager.SaveScene(scene,ScenePath);EditorBuildSettings.scenes=new[]{new EditorBuildSettingsScene(ScenePath,true)};AssetDatabase.SaveAssets();
        }
    }
}
