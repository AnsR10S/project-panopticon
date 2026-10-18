# Architecture diagram sources

`definitions.json` is the common graph source for the new V1 diagrams. Run
`python scripts/render_diagrams.py` from the installed project environment to
generate the matching Mermaid, SVG, and PNG files. The Mermaid exports are
editable and useful in other tools; update the common graph definition when
changing the version controlled architecture so rendered and code views agree.

Rendering uses Matplotlib from the dependency lock, without external browser
downloads or a network renderer. All three generated PNGs were visually checked
for orientation, clipping, text overlap, and phase order. The older figures and
Mermaid sources remain unchanged in `docs/source-materials/research-package-v1`.
