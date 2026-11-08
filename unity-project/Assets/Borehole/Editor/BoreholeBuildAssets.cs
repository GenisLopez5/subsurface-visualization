using System.IO;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;
namespace Subsurface.Borehole.Editor
{
    public sealed class BoreholeBuildAssets : IPreprocessBuildWithReport
    {
        public int callbackOrder=>-1000;
        public void OnPreprocessBuild(BuildReport report)=>Ensure();
        [MenuItem("Tools/Lithology/Prepare Player Render Assets")]
        public static void Ensure()
        {
            const string folder="Assets/Borehole/Resources";Directory.CreateDirectory(folder);AssetDatabase.Refresh();
            Material Material(string name,string shader,Color color)
            {
                string path=folder+"/"+name+".mat";var material=AssetDatabase.LoadAssetAtPath<Material>(path);
                if(material==null){var found=Shader.Find(shader);if(found==null)throw new BuildFailedException("Required shader missing: "+shader);material=new Material(found);AssetDatabase.CreateAsset(material,path);}
                if(material.HasProperty("_BaseColor"))material.SetColor("_BaseColor",color);else if(material.HasProperty("_Color"))material.SetColor("_Color",color);if(material.HasProperty("_Smoothness"))material.SetFloat("_Smoothness",.08f);EditorUtility.SetDirty(material);return material;
            }
            string assetPath=folder+"/BoreholeRenderAssets.asset";var assets=AssetDatabase.LoadAssetAtPath<BoreholeRenderAssets>(assetPath);
            if(assets==null){assets=ScriptableObject.CreateInstance<BoreholeRenderAssets>();AssetDatabase.CreateAsset(assets,assetPath);}
            assets.skybox=Material("BoreholeSky","Skybox/Procedural",Color.white);assets.skybox.SetFloat("_SunSize",.025f);assets.skybox.SetFloat("_AtmosphereThickness",1);
            assets.soil=Material("BoreholeSoil","Universal Render Pipeline/Lit",new Color(.34f,.25f,.19f));assets.ground=Material("BoreholeSurface","Universal Render Pipeline/Lit",new Color(.19f,.27f,.09f));
            assets.selectionShader=Shader.Find("Universal Render Pipeline/Unlit");if(assets.selectionShader==null)throw new BuildFailedException("Selection shader is missing.");
            Material("BoreholeSelection","Universal Render Pipeline/Unlit",new Color(.47f,1,0));EditorUtility.SetDirty(assets);EditorUtility.SetDirty(assets.skybox);AssetDatabase.SaveAssets();
        }
    }
}
