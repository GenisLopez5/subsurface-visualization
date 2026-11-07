# Fix verification

Implemented CSV visibility preservation, coherent density-based label hiding, alternating label sides, and a compressed surface-to-log break. The underlying interval geometry remains rebased to its logged start. No interval from zero to the first source row was invented.

29/29 Borehole.Tests passed. Live checks passed for closed/open CSV selection behavior, overview label hiding, alternating label positions, constant-size depth break at both 1,000 and 1,000,000 ft, no break for zero, unchanged first-section mesh position, and renderer fallback diagnostics on all 94 unknown source intervals.

MaterialAudit.json records every distinct supplied label, source count, lookup result, definition and schematic status. The repetition is due to unresolved catalogue labels and a generic salt recipe, not a failure to assign materials. No catalogue meanings or aliases were guessed. AlternatingLabels.png is a small synthetic layout fixture; DepthBreak.png uses the supplied CSV and its incompatible preview image, which remains explicitly unlinked.

Pipeline documentation used: commands/scripts.md and commands/capture.md, with the existing compile/play-mode verification workflow. Restore point: Temp/IterationRestore/Before-labels-depth-gap.zip.

Final live checks repeated successfully after correcting a stylesheet write error. Final Console had zero errors/warnings. Play mode is stopped and the original Game-view settings and Run In Background value are restored. Auto-tick was already enabled and remains enabled. Native resource cleanup was checked after exit.
