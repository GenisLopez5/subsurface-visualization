using System.Collections.Generic;
using UnityEngine;
namespace Subsurface.Lithology
{
    [CreateAssetMenu(menuName="Lithology/Definition")]
    public class LithologyDefinition : ScriptableObject
    {
        public string stableId, rawLabel, provisionalNormalizedName;
        public RenderRole role;
        public InterpretationStatus interpretation;
        public bool requiresSourceLegend, sourceVerified, prefixExpanded;
        [TextArea] public string sourceInstructions;
        public List<SourceLocation> sourceLocations = new List<SourceLocation>();
        public List<BaseTextureSet> optionalComponentSources = new List<BaseTextureSet>();
        public List<BaseTextureSet> possibleButUnselectedBases = new List<BaseTextureSet>();
        public LithologyMaterialRecipe generatedRecipe;
        [Tooltip("User-confirmed interpretation, kept separately and preserved by initialization.")]
        public LithologyMaterialRecipe confirmedRecipe;
        [TextArea] public string confirmationEvidence;
        public LithologyMaterialRecipe EffectiveRecipe => confirmedRecipe != null ? confirmedRecipe : generatedRecipe;
    }
}
