#if DEVELOPMENT_BUILD || UNITY_EDITOR
using System;
using System.Collections;
using Subsurface.Borehole;
using UnityEngine;
using UnityEngine.UIElements;
namespace Subsurface.Explorer
{
    public sealed class PlayerRenderSmokeTest : MonoBehaviour
    {
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]static void Launch()
        {if(Array.IndexOf(Environment.GetCommandLineArgs(),"-borehole-smoke-test")>=0)new GameObject("Player rendering verification").AddComponent<PlayerRenderSmokeTest>();}
        IEnumerator Start()
        {
            yield return null;yield return null;var explorer=FindAnyObjectByType<DocumentExplorer>();var viewer=explorer!=null?explorer.viewer:null;
            if(viewer==null||viewer.Root==null||viewer.Root.Q("zoomSlider")==null){Finish(false,"Viewer did not initialize");yield break;}
            var assets=BoreholeRenderAssets.Load();
            if(!assets.soil.shader.isSupported||!assets.skybox.shader.isSupported||GameObject.Find("3D cutaway ground")==null){Finish(false,"Terrain or shaders unavailable");yield break;}
            if(!viewer.LoadCsvText("top_depth,bottom_depth,lithology\n0,50,Sandstone\n50,100,Shale","Player verification")){Finish(false,viewer.LastError);yield break;}
            viewer.Root.style.display=DisplayStyle.Flex;yield return null;yield return null;
            double before=viewer.WindowFeet;viewer.Zoom(.5);yield return null;yield return null;
            if(viewer.WindowFeet>=before||viewer.columnCamera.targetTexture==null||!viewer.columnCamera.targetTexture.IsCreated()||viewer.column.Surfaces.Count!=2){Finish(false,"Zoom or render target failed");yield break;}
            foreach(var surface in viewer.column.Surfaces)if(surface.target.sharedMaterial==null||!surface.target.sharedMaterial.shader.isSupported){Finish(false,"Lithology shader unavailable");yield break;}
            Finish(true,"Terrain, sky, lithology materials, render target, and zoom initialized in standalone Player");
        }
        static void Finish(bool ok,string detail){Debug.Log("BOREHOLE_PLAYER_"+(ok?"PASS: ":"FAIL: ")+detail);Application.Quit(ok?0:1);}
    }
}
#endif
