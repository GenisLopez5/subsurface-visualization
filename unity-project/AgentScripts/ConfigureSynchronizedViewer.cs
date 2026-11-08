using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Subsurface.Borehole;
using Subsurface.Borehole.Editor;

public static class ConfigureSynchronizedViewer
{
    public static string Run()
    {
        if(EditorApplication.isPlaying)throw new InvalidOperationException("Exit Play mode first.");
        const string imagePath="Assets/Borehole/Samples/Allegheny-preview.png";
        var importer=(TextureImporter)AssetImporter.GetAtPath(imagePath);importer.maxTextureSize=8192;importer.npotScale=TextureImporterNPOTScale.None;importer.mipmapEnabled=false;importer.textureCompression=TextureImporterCompression.Uncompressed;importer.SaveAndReimport();
        BoreholeViewerBuilder.CreateOrOpen();var viewer=Resources.FindObjectsOfTypeAll<BoreholeViewer>().First(v=>v.gameObject.scene.path==BoreholeViewerBuilder.ScenePath);
        viewer.initialCsv=AssetDatabase.LoadAssetAtPath<TextAsset>("Assets/Borehole/Samples/Allegheny.csv");viewer.initialDocumentImage=AssetDatabase.LoadAssetAtPath<Texture2D>(imagePath);viewer.initialSourceName="Allegheny Attic · GC297 #3 · Mud log";
        viewer.columnCamera.backgroundColor=new Color(.545f,.396f,.302f,1);EditorUtility.SetDirty(viewer);EditorSceneManager.MarkSceneDirty(viewer.gameObject.scene);EditorSceneManager.SaveScene(viewer.gameObject.scene);AssetDatabase.SaveAssets();
        return "Split viewer configured with user CSV and explicitly incompatible PNG preview (425x6736).";
    }
}
