# Visualizer iteration checklist

- [x] Remove viewport frames, margins, pane headings and large branding.
- [x] Wheel and vertical drag navigation; disable orbit/tilt.
- [x] Click-only selection, preserved during navigation.
- [x] Real cutaway ground mesh and camera skybox; document header pins camera at the surface.
- [x] Shared zoom with document fit and conditional highlight window.
- [x] Collapsible 50% CSV overlay; independent scrolling, click navigation in both directions.
- [x] Verify layout and interactions in Unity; correct any issues found.

Restore point: Temp/IterationRestore/Before-space-navigation.zip

Follow-up fixes:
- [x] Keep CSV closed when selecting a section.
- [x] Alternate readable labels; hide all labels at overview/dense zoom.
- [x] Audit repeated materials and document unmatched labels / schematic salt.
- [x] Add a labeled, unscaled break for nonzero log starts.

Final visual refinements:
- [x] Connect section tags to cylinder edges with lines.
- [x] Reduce cylinder pane width approximately 20%, center the column, cover the underground right edge with 3D ground.
- [x] Highlight selected document pixels independently of the navigation window; retain safe behavior for gaps and mismatched images.

- [x] Keep permanent alternating sides; anchor unselected tags at true midpoints and let only selected tags follow the camera.
