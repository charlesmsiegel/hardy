# The Hardy web client

The page `hardy web` serves. React and Vite, built into
`src/hardy/app/web/static/` and committed there, so a clone that never
installs Node still has a working browser client.

## Install

```sh
cd web
npm ci
```

`npm ci` installs exactly what `package-lock.json` pins. Use `npm install`
only when you mean to move a version.

## Build

```sh
npm run build
```

This runs Vite into `../src/hardy/app/web/static/` (emptying it first) and
then `notices.mjs`, which writes `THIRD-PARTY-NOTICES.txt` beside the bundle.
**Commit the result.** The Python package ships the built files; a change to
anything under `web/src/` that is not rebuilt and committed is a change the
served page does not have.

`npm run dev` serves the same sources on Vite's own port, but the API and the
event stream are not there, so the page loads and stays empty. The useful loop
is `npm run build` against the smoke server below.

## Constraints the build has to keep

The server sends a strict Content-Security-Policy:

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
font-src 'self'; img-src 'self' data:; object-src 'self';
frame-ancestors 'none'; connect-src 'self'
```

So: no CDN, no inline `<script>`, no `data:` font or stylesheet. `vite.config.js`
turns off the module-preload polyfill (Vite injects it inline) and the asset
inlining threshold (a small asset would become a `data:` URI), and sets
`base: './'` because the page is also served from unknown routes the client
routes itself.

`index.html` must keep `<meta name="hardy-token" content="__HARDY_TOKEN__">`.
The server replaces that placeholder on every serve with the token for this
process, and every mutation the page makes echoes it in `X-Hardy-Token`. A
build that drops the meta produces a page that can read the session and never
act on it.

## Smoke

Two shells, from the repository root:

```sh
uv run python tests/unit/web_smoke_server.py --port 8765
```

```sh
cd web && npm run smoke
```

The server builds a `WebHost` over the same fake session the unit tests use
and serves the real `static/` directory. The smoke fetches the page, checks
the token meta is there and that every script and stylesheet it references
loads, opens the event stream, sends `hello`, and waits for the reply event.
It prints `smoke ok`.
