using UnityEngine;

namespace Subsurface.Lithology
{
    [CreateAssetMenu(menuName="Lithology/Base Texture Set")]
    public class BaseTextureSet : ScriptableObject
    {
        public string stableId, description;
        [TextArea] public string procurementGuidance;
        public Texture2D albedo, normal, surface, height, componentMask;
        public Texture2D detailAlbedo, detailNormal, detailMask;
        [Tooltip("Custom surface: R metallic, G AO, B unused, A smoothness. Linear import.")]
        public string surfaceLayout = "R Metallic / G AO / B unused / A Smoothness";
        public bool illustrativePreview = true, conditional, independentGrainScale = true, hasSeparatedMacro = true;
        public bool supportsHeightDisplacement; // This shader never displaces geometry.
        public bool invertNormalGreen;
        [Min(.00001f)] public float macroTileMetres = 1, detailTileMetres = .01f;
        [Range(0,1)] public float detailColorStrength = .4f;
        [TextArea] public string sourceNotes = "Illustrative deterministic procedural preview. No specimen observations.", licenseNotes = "Generated locally for this project; no external scan.";
        public string sourceUrl, physicalScaleEvidence = "Unknown; numerical defaults are artistic";
        public int revision = 1;
    }
}
