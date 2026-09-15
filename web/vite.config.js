import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';

// The bundle is served by `hardy web` from inside the package, under a strict
// CSP: `script-src 'self'` and `font-src 'self'`. Three settings here exist
// only to keep that true.
//
// * `base: './'` -- the page is served from `/` but also from every unknown
//   route (the client routes `/files/lean` itself), so absolute asset URLs
//   would resolve differently depending on which route served the page.
// * `modulePreload.polyfill: false` -- Vite otherwise injects the polyfill as
//   an *inline* module script, which `script-src 'self'` refuses outright.
// * `assetsInlineLimit: 0` -- a small asset inlined as a `data:` URI would be
//   a font or a stylesheet arriving from `data:`, which `font-src 'self'`
//   refuses. Every asset gets a file of its own instead.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../src/hardy/app/web/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    assetsInlineLimit: 0,
    modulePreload: {polyfill: false},
  },
});
