# Platform support for Memory

Memory's only runtime libraries use import-map **bare specifiers** resolved
same-origin from the Mobius shell's `/vendor` importmap — no `esm.sh` fallback:

- Markdown rendering: `import('marked')` + `import('dompurify')` (declared in
  `mobius.json` `runtime.imports`). The shell vendors both (the `app-frame.html`
  importmap + `backend/app/runtime_libs.py` `RUNTIME_LIBS`), exactly like Notes.
- Graph rendering and force layout are implemented with browser-native SVG and
  JavaScript. They have no external runtime dependency and work without WebGL.

The platform can remove the Memory-only classic D3/Pixi vendor files and service
worker precache entries. If a future renderer needs an additional library,
declare one supported bare specifier in `mobius.json` `runtime.imports` and add
that same specifier to the platform's canonical runtime registry. Do not add a
second app-local script loader.
