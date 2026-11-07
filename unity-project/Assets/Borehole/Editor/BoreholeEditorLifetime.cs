using UnityEditor;
using UnityEngine;

namespace Subsurface.Borehole.Editor
{
    [InitializeOnLoad]public static class BoreholeEditorLifetime
    {
        static BoreholeEditorLifetime()
        {
            AssemblyReloadEvents.beforeAssemblyReload+=ReleaseColumns;
            EditorApplication.quitting+=ReleaseColumns;
            EditorApplication.playModeStateChanged+=state=>{if(state==PlayModeStateChange.ExitingPlayMode)ReleaseColumns();};
            EditorApplication.delayCall+=RemoveOrphans;
        }
        static void ReleaseColumns()
        {
            foreach(var column in Object.FindObjectsByType<BoreholeColumn>(FindObjectsInactive.Include))column.ReleaseRuntimeResources(true);
            RemoveOrphans();
        }
        public static void RemoveOrphans()
        {
            var filters=Resources.FindObjectsOfTypeAll<MeshFilter>();
            var colliders=Resources.FindObjectsOfTypeAll<MeshCollider>();
            foreach(var mesh in Resources.FindObjectsOfTypeAll<Mesh>())
            {
                if(mesh.name!="Shared capped borehole cylinder"||EditorUtility.IsPersistent(mesh)||(mesh.hideFlags&HideFlags.DontSave)==0)continue;
                bool used=false;foreach(var f in filters)if(f!=null&&f.sharedMesh==mesh){used=true;break;}if(!used)foreach(var c in colliders)if(c!=null&&c.sharedMesh==mesh){used=true;break;}
                if(!used)Object.DestroyImmediate(mesh);
            }
            var renderers=Resources.FindObjectsOfTypeAll<LineRenderer>();
            foreach(var material in Resources.FindObjectsOfTypeAll<Material>())
            {
                if(material.name!="Borehole selection"||EditorUtility.IsPersistent(material)||(material.hideFlags&HideFlags.DontSave)==0)continue;
                bool used=false;foreach(var r in renderers)if(r!=null&&r.sharedMaterial==material){used=true;break;}if(!used)Object.DestroyImmediate(material);
            }
        }
    }
}
