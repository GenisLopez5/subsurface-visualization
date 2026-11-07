using System.Collections.Generic;
using UnityEngine;
namespace Subsurface.Lithology
{
    [CreateAssetMenu(menuName="Lithology/Material Recipe")]
    public class LithologyMaterialRecipe : ScriptableObject
    {
        public string stableId;
        public BaseTextureSet baseTexture;
        public List<ComponentLayer> componentLayers = new List<ComponentLayer>();
        public List<ModifierSettings> modifiers = new List<ModifierSettings>();
        public CoordinateSettings coordinates = new CoordinateSettings();
        public StructuralSettings structure = new StructuralSettings();
        public Color palette = Color.white, mineralLight = new Color(.83f,.8f,.73f), mineralDark = new Color(.14f,.16f,.17f);
        [Range(0,1)] public float mineralDarkCoverage = .3f;
        [Min(.00001f)] public float grainMetres = .002f;
        [Min(.001f)] public float schematicExaggeration = 8;
        [Range(0,2)] public float normalStrength = .55f;
        [Range(0,1)] public float smoothness = .25f;
        public int seed = 1001, revision = 1;
        public bool numericalDefaultsAreIllustrative = true;
        [TextArea] public string notes = "Artistic display parameters; not measured mineral fractions, strength, or specimen dimensions.";
    }
}
