using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Subsurface.Lithology.Editor
{
    public static class LithologyValidation
    {
        [MenuItem("Tools/Lithology/Validate Catalogue and Materials",priority=35)]
        public static void Validate()
        {
            var errors=Check(LithologyBuilder.Catalog);string report="Lithology validation: "+(errors.Count==0?"PASS":"FAIL")+"\n"+string.Join("\n",errors);
            Directory.CreateDirectory(LithologyBuilder.Output+"/Verification");File.WriteAllText(LithologyBuilder.Output+"/Verification/Validation.txt",report);
            if(errors.Count>0)throw new InvalidOperationException(report);Debug.Log(report);
        }
        public static List<string> Check(LithologyCatalog c)
        {
            var errors=new List<string>();if(c==null){errors.Add("No catalogue");return errors;}
            void Need(bool b,string e){if(!b)errors.Add(e);}
            Need(c.definitions.Count==180,"Expected 180 definitions");Need(c.definitions.Select(d=>d.rawLabel).Distinct().Count()==180,"Raw labels must remain distinct");Need(c.definitions.Select(d=>d.stableId).Distinct().Count()==180,"IDs must remain distinct");Need(c.definitions.Sum(d=>d.sourceLocations.Count)==182,"Expected 182 source occurrences");
            Need(c.bases.Count==25&&c.bases.Select(b=>b.stableId).Distinct().Count()==25,"Expected B01-B25");Need(c.modifiers.Count==53&&c.modifiers.Select(m=>m.stableId).Distinct().Count()==53,"Expected 53 semantic IDs");
            Need(c.definitions.Count(d=>d.role==RenderRole.SourceCode)==25,"Expected 25 opaque codes");
            Need(c.shader!=null&&c.shader.isSupported,"Shader missing or unsupported");
            if(c.shader!=null)foreach(var msg in ShaderUtil.GetShaderMessages(c.shader))if(msg.severity==UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error)errors.Add("Shader: "+msg.message);
            foreach(var d in c.definitions)
            {
                Need(d.generatedRecipe!=null,d.rawLabel+": missing recipe");if(d.generatedRecipe==null)continue;
                Need(d.generatedRecipe.baseTexture==null||c.bases.Contains(d.generatedRecipe.baseTexture),d.rawLabel+": selected base is not registered");
                if(d.role==RenderRole.SourceCode)Need(d.generatedRecipe.baseTexture==null&&d.requiresSourceLegend,d.rawLabel+": opaque code decoded");
                Need(c.Resolve(d.rawLabel).definition==d,d.rawLabel+": exact resolver failed");
                foreach(var m in d.generatedRecipe.modifiers)Need(c.GetModifier(m.id)!=null,d.rawLabel+": unresolved modifier "+m.id);
                var compiled=LithologyRecipeCompiler.Compile(c,d);Need(compiled.valid,d.rawLabel+": "+string.Join("; ",compiled.diagnostics));
                foreach(var source in d.optionalComponentSources)Need(c.bases.Contains(source),d.rawLabel+": missing component source");
                foreach(var candidate in d.possibleButUnselectedBases)Need(c.bases.Contains(candidate),d.rawLabel+": missing candidate");
            }
            return errors;
        }
        public static void Capture()
        {
            var ctrl=UnityEngine.Object.FindAnyObjectByType<LithologyDemoController>();if(ctrl==null)throw new InvalidOperationException("Open demo first");
            string folder=LithologyBuilder.Output+"/Verification";Directory.CreateDirectory(folder);
            foreach(int x in new[]{0,32,64})
            {
                ctrl.Frame(x);var camera=ctrl.viewCamera;var old=camera.targetTexture;var active=RenderTexture.active;var rect=camera.rect;
                var rt=RenderTexture.GetTemporary(1500,1100,24,RenderTextureFormat.ARGB32);var read=new Texture2D(1500,1100,TextureFormat.RGB24,false);
                try {camera.rect=new Rect(0,0,1,1);camera.targetTexture=rt;camera.Render();RenderTexture.active=rt;read.ReadPixels(new Rect(0,0,1500,1100),0,0);read.Apply();File.WriteAllBytes(folder+"/"+(x==0?"Families":x==32?"Recipes":"Cutaway")+".png",read.EncodeToPNG());}
                finally{camera.targetTexture=old;camera.rect=rect;RenderTexture.active=active;RenderTexture.ReleaseTemporary(rt);UnityEngine.Object.DestroyImmediate(read);}
            }
            ctrl.Frame(0);AssetDatabase.Refresh();Debug.Log("Lithology: captured rendered gallery, recipes and cutaway.");
        }
    }
}
