using UnityEngine;
namespace Subsurface.Lithology
{
    [CreateAssetMenu(menuName="Lithology/Modifier Definition")]
    public class ModifierDefinition : ScriptableObject
    {
        public string stableId;
        [TextArea] public string constraint;
        public ModifierOperation operation;
        public MaskKind defaultMask;
        public bool requiresKnownHost = true, requiresReference;
        public string applicability = "Known host; artistic visual treatment, never a measured fraction";
    }
}
