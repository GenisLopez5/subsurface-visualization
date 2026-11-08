using UnityEngine;
namespace Subsurface.Lithology
{
    public class LithologyIntegrationExample : MonoBehaviour
    {
        public LithologyCatalog catalogue;
        public LithologyRenderer surface;
        void Start()
        {
            if(catalogue==null||surface==null)return;
            var result=catalogue.Resolve("  fine sandstone  ");
            surface.catalog=catalogue;surface.materialSlot=0;surface.SetDefinition(result.definition);
            surface.SetOverrides(new VisualOverrides{grainMultiplier=1.5f});surface.SetDisplayMode(DisplayMode.Texture);
        }
    }
}
