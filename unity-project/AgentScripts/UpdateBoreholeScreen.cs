using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.Rendering.Universal;
using Subsurface.Borehole;
using Subsurface.Borehole.Editor;

public static class UpdateBoreholeScreen
{
    public static string Run()
    {
        var viewer=Object.FindAnyObjectByType<BoreholeViewer>();
        if(viewer.presentationCamera==null)
        {
            var go=new GameObject("Document screen camera");SceneManager.MoveGameObjectToScene(go,viewer.gameObject.scene);var camera=go.AddComponent<Camera>();camera.tag="MainCamera";camera.cullingMask=0;camera.depth=1;camera.backgroundColor=new Color(.063f,.094f,.114f);camera.clearFlags=CameraClearFlags.SolidColor;go.AddComponent<UniversalAdditionalCameraData>();viewer.presentationCamera=camera;
        }
        viewer.columnCamera.tag="Untagged";viewer.columnCamera.depth=-10;EditorUtility.SetDirty(viewer);EditorSceneManager.SaveScene(viewer.gameObject.scene,BoreholeViewerBuilder.ScenePath);return "Presentation camera saved";
    }
}
