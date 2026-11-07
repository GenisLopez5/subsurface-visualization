using System;
using System.Collections.Generic;
using UnityEngine;

namespace Subsurface.Lithology
{
    public enum RenderRole { Physical, Procedural, Component, Descriptor, ContextDependent, GenericClass, DataStatus, SourceCode }
    public enum InterpretationStatus { Illustrative, NeedsVerification, Confirmed }
    public enum DisplayMode { Texture, Legend }
    public enum ProjectionSpace { GeologicalWorld, ObjectRest }
    public enum ResolutionStatus { Exact, Normalized, VerifiedAlias, Unknown, Invalid, Ambiguous }
    public enum MaskKind { FineMixture, Patches, Grain, RoundedClast, AngularClast, Shell, Ooid, Crystal, Glass, Flake, Vein, Pore, Welded, Lamination }
    public enum ModifierOperation { Palette, GrainScale, Consolidation, Component, Bedding, Cleavage, SupportFabric, Sheen, Banding, Welding, Sorting, Recrystallization, MineralBalance, Metadata, Status, Geometry, Conditional }

    [Serializable] public class SourceLocation
    {
        public string source = "Supplied implementation brief / pasted extraction";
        public int mappingRow;
        public int briefLine;
        public string extractionLocation = "Unknown";
        public string pdfFile = "Unknown", pdfPage = "Unknown", codebook = "Unknown";
    }
    [Serializable] public class VerifiedAlias { public string label; public LithologyDefinition target; public string verificationEvidence; }
    [Serializable] public class ModifierSettings
    {
        public string id;
        public string value;
        public bool optional, enabled = true, referenceConfirmed;
        [Range(0,1)] public float strength = .32f;
        public MaskKind occurrence = MaskKind.FineMixture;
    }
    [Serializable] public class ComponentLayer
    {
        public string semanticId;
        public BaseTextureSet textureSource;
        public MaskKind mask;
        public Color color = new Color(.65f,.62f,.5f);
        [Range(0,1)] public float coverage = .32f, smoothness = .25f, metallic;
        [Min(.00001f)] public float spacingMetres = .004f;
        [Range(-1,2)] public float relief = .3f;
    }
    [Serializable] public class CoordinateSettings
    {
        public bool triplanar = true;
        public ProjectionSpace space = ProjectionSpace.GeologicalWorld;
        [Min(.00001f)] public float metresPerUnityUnit = 1f;
        public Vector3 geologicalOrigin;
        public Vector3 beddingNormal = Vector3.up;
        public Vector3 cleavageNormal = new Vector3(.7f,.7f,0);
    }
    [Serializable] public class StructuralSettings
    {
        [Min(.00001f)] public float beddingSpacingMetres = .12f, cleavageSpacingMetres = .08f,
            fractureSpacingMetres = .45f, clastSpacingMetres = .12f, veinWidthMetres = .004f, mottleSpacingMetres = .5f;
        [Range(0,1)] public float bedding, cleavage, fractures, banding, welding, poorSorting;
        [Range(0,1)] public float irregularity = .15f;
        public bool carbonateMudPresent = true;
        [Range(0,1)] public float grainSupport = .3f;
    }
    [Serializable] public class VisualOverrides
    {
        public bool overridePalette;
        public Color tint = Color.white;
        [Min(.001f)] public float grainMultiplier = 1, exaggerationMultiplier = 1;
        [Range(0,2)] public float normalMultiplier = 1;
        public bool optionalLamination;
        public bool overrideCoordinates;
        public CoordinateSettings coordinates = new CoordinateSettings();
    }
    public sealed class ResolutionResult
    {
        public string originalInput, diagnostic;
        public ResolutionStatus status;
        public LithologyDefinition definition;
        public bool IsResolved => definition != null && status != ResolutionStatus.Ambiguous;
    }
    [Serializable] public class SeedRow
    {
        public string id, rawLabel, selectedBaseId, instructions, role, normalizedCandidate;
        public int mappingRow, briefLine, occurrences = 1;
        public string[] extractionLocations, componentSourceIds, candidateBaseIds;
        public bool prefixExpanded;
    }
    [Serializable] public class SeedModifier { public string id, instruction; }
    [Serializable] public class SeedBase { public string id, family, guidance; }
    [Serializable] public class SeedData { public int schemaVersion; public SeedRow[] rows; public SeedModifier[] modifiers; public SeedBase[] bases; }
}
