import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// The bundle is served by `hardy web` from inside the package, under a strict
// CSP: `script-src 'self'` and `font-src 'self'`. Three settings here exist
// only to keep that true.
//
// * `base: '/'` -- asset URLs must be root-absolute. The server serves the one
//   bundle from the root and answers every unknown route with `index.html`,
//   because the client routes `/files/lean` itself. Relative URLs would then
//   resolve against the route rather than the root: a page served at
//   `/files/lean` would ask for `/files/assets/index-*.js`, the fallback would
//   answer it with `index.html` as `text/html`, and the page would be blank.
// * `modulePreload.polyfill: false` -- Vite otherwise injects the polyfill as
//   an *inline* module script, which `script-src 'self'` refuses outright.
// * `assetsInlineLimit: 0` -- a small asset inlined as a `data:` URI would be
//   a font or a stylesheet arriving from `data:`, which `font-src 'self'`
//   refuses. Every asset gets a file of its own instead.
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../src/hardy/app/web/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    assetsInlineLimit: 0,
    modulePreload: {polyfill: false},
  },
});
