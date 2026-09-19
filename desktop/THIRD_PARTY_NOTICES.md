# Desktop third-party notices

## Home mascot visual assets

AEGIS Desktop includes the home-mascot image assets copied from the
[Pi Desktop repository](https://github.com/earendil-works/pi):

- `src/assets/assistant-mascot-light.gif`
- `src/assets/assistant-mascot-dark.gif`
- `src/assets/assistant-mascot-still-light.png`
- `src/assets/assistant-mascot-still-dark.png`

The animated GIFs are used by the home screen; the still images remain as
reduced-motion-safe source assets. The sidebar mark is an original inline AEGIS
SVG and no upstream product logo is bundled or rendered. These mascot assets
are retained only for the requested visual direction. The upstream repository
publishes its project under GNU LGPL v3.0; keep the upstream license and
attribution with any redistributed desktop bundle, and review the upstream
asset/trademark terms before public distribution. AEGIS application code and
the AEGIS backend remain separate from the upstream runtime code.

## Lucide React icons

The renderer uses `lucide-react@1.31.0` for the chrome and composer icons,
matching Pi Desktop's icon family. Lucide is distributed under the ISC
license; its upstream license text is included in
`node_modules/lucide-react/LICENSE` and must remain available in redistributed
bundles.
