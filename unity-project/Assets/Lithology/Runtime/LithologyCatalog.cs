using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using UnityEngine;
namespace Subsurface.Lithology
{
    [CreateAssetMenu(menuName="Lithology/Catalogue")]
    public class LithologyCatalog : ScriptableObject
    {
        public Shader shader;
        public List<LithologyDefinition> definitions = new List<LithologyDefinition>();
        public List<BaseTextureSet> bases = new List<BaseTextureSet>();
        public List<ModifierDefinition> modifiers = new List<ModifierDefinition>();
        public List<VerifiedAlias> verifiedAliases = new List<VerifiedAlias>();
        public int revision = 1;
        Dictionary<string,List<LithologyDefinition>> exact, normalized, aliases;
        Dictionary<string,ModifierDefinition> modifierLookup;
        static string Key(string s) => Regex.Replace(s.Trim(), @"\s+", " ").ToUpperInvariant();
        static void Add(Dictionary<string,List<LithologyDefinition>> map, string key, LithologyDefinition d)
        { if (!map.TryGetValue(key,out var list)) map[key] = list = new List<LithologyDefinition>(); if (!list.Contains(d)) list.Add(d); }
        public void RebuildLookups()
        {
            exact = new Dictionary<string,List<LithologyDefinition>>(StringComparer.Ordinal);
            normalized = new Dictionary<string,List<LithologyDefinition>>(StringComparer.Ordinal);
            aliases = new Dictionary<string,List<LithologyDefinition>>(StringComparer.Ordinal);
            modifierLookup = new Dictionary<string,ModifierDefinition>(StringComparer.Ordinal);
            foreach (var d in definitions) if (d != null && !string.IsNullOrWhiteSpace(d.rawLabel)) { Add(exact,d.rawLabel,d); Add(normalized,Key(d.rawLabel),d); }
            foreach (var a in verifiedAliases) if (a.target != null && !string.IsNullOrWhiteSpace(a.label) && !string.IsNullOrWhiteSpace(a.verificationEvidence)) Add(aliases,Key(a.label),a.target);
            foreach (var m in modifiers) if (m != null && !modifierLookup.ContainsKey(m.stableId)) modifierLookup.Add(m.stableId,m);
        }
        void OnEnable() => RebuildLookups();
        void OnValidate() { exact = null; }
        public ModifierDefinition GetModifier(string id) { if (exact == null) RebuildLookups(); return id != null && modifierLookup.TryGetValue(id,out var m) ? m : null; }
        public ResolutionResult Resolve(string raw)
        {
            var r = new ResolutionResult { originalInput = raw, status = ResolutionStatus.Invalid, diagnostic = "Null, empty, or whitespace label" };
            if (string.IsNullOrWhiteSpace(raw)) return r;
            if (exact == null) RebuildLookups();
            if (exact.TryGetValue(raw,out var matches)) return Finish(r,matches,ResolutionStatus.Exact);
            // Aliases never override a conflicting normalized source record.
            var combined = new List<LithologyDefinition>();
            bool hasSource = normalized.TryGetValue(Key(raw),out matches);
            if (hasSource) combined.AddRange(matches);
            if (aliases.TryGetValue(Key(raw),out matches)) foreach(var d in matches) if (!combined.Contains(d)) combined.Add(d);
            if (combined.Count > 0) return Finish(r,combined,hasSource ? ResolutionStatus.Normalized : ResolutionStatus.VerifiedAlias);
            r.status = ResolutionStatus.Unknown; r.diagnostic = "Unrecognized input retained; no geological interpretation inferred."; return r;
        }
        static ResolutionResult Finish(ResolutionResult r,List<LithologyDefinition> ds,ResolutionStatus status)
        { r.status = ds.Count == 1 ? status : ResolutionStatus.Ambiguous; r.definition = ds.Count == 1 ? ds[0] : null; r.diagnostic = ds.Count == 1 ? status.ToString() : "Lookup collision: " + string.Join(", ",ds.ConvertAll(d=>d.stableId)); return r; }
    }
}
