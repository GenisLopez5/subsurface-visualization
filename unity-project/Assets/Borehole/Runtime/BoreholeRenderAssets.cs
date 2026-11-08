using UnityEngine;
namespace Subsurface.Borehole
{
    public sealed class BoreholeRenderAssets : ScriptableObject
    {
        public Material skybox,soil,ground;
        public Shader selectionShader;
        public static BoreholeRenderAssets Load()
        {
            var assets=Resources.Load<BoreholeRenderAssets>("BoreholeRenderAssets");
            if(assets==null||assets.skybox==null||assets.soil==null||assets.ground==null||assets.selectionShader==null)throw new System.InvalidOperationException("Borehole render assets are missing. Build using the project's configured build pipeline.");
            return assets;
        }
    }
}
