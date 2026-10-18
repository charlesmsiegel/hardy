import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import React, {act} from 'react';
const dom = new JSDOM('<!doctype html><div id="root"></div>', {url: 'http://localhost/'});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createRoot} = await import('react-dom/client');
const server = await createServer({server: {middlewareMode: true, ws: false}});
const {default: Editor} = await server.ssrLoadModule('/src/files/Editor.jsx');
const root = createRoot(document.getElementById('root'));
let props;
const draw = async (next = {}) => {
  props = {...props, ...next};
  await act(() => root.render(React.createElement(Editor, props)));
};
const type = async (text) => {
  const area = document.querySelector('textarea');
  await act(() => {
    Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set.call(area, text);
    area.dispatchEvent(new window.Event('input', {bubbles: true}));
  });
};
const click = async (label) => {
  const button = [...document.querySelectorAll('button')].find((b) => b.textContent === label);
  assert.ok(button, label);
  await act(() => button.click());
};
const buffer = () => document.querySelector('textarea').value;
test.after(async () => { await act(() => root.unmount()); await server.close(); dom.window.close(); });
test.beforeEach(async () => {
  await act(() => root.render(null));
  props = {path:'lean/A.lean', kind:'lean', text:'original', sha256:'old', verdict:null};
  await draw();
});
test('dirty buffer survives disk change until explicit reload', async () => {
  assert.equal(document.querySelector('textarea').getAttribute('aria-label'), 'Edit lean/A.lean');
  await type('my edits');
  await draw({text:'external', sha256:'new'});
  assert.equal(buffer(), 'my edits');
  assert.match(document.body.textContent, /changed on disk/);
  await click('Reload from disk (discards edits)');
  assert.equal(buffer(), 'external');
});
test('clean refresh and switching paths replace the buffer', async () => {
  await draw({text:'external', sha256:'new'});
  assert.equal(buffer(), 'external');
  await type('my edits');
  await draw({path:'tex/B.tex', kind:'tex', text:'other', sha256:'other'});
  assert.equal(buffer(), 'other');
});
test('late successful save keeps edits typed during the request', async () => {
  let finish;
  globalThis.fetch = async () => new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({ok:true, output:'saved', sha256:'saved'}))); });
  await type('submitted');
  await click('Save & check');
  await type('newer edits');
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  await draw({text:'submitted', sha256:'saved'});
  assert.equal(buffer(), 'newer edits');
  assert.doesNotMatch(document.body.textContent, /changed on disk/);
});
test('deleted disk file preserves edits and offers explicit recreate', async () => {
  await type('my edits');
  await draw({text:'', sha256:null, missing:true});
  assert.equal(buffer(), 'my edits');
  assert.match(document.body.textContent, /deleted on disk/);
  assert.ok([...document.querySelectorAll('button')].some((b) => b.textContent === 'Keep edits and recreate'));
});
test('save sends loaded digest and conflict requires an explicit overwrite', async () => {
  const sent = [];
  globalThis.fetch = async (url, request) => {
    sent.push(JSON.parse(request.body));
    return new Response(JSON.stringify({error:'changed on disk', conflict:true, text:'external', sha256:'new'}), {status:409});
  };
  await type('my edits');
  await click('Save & check');
  assert.equal(sent[0].base_sha256, 'old');
  assert.equal(buffer(), 'my edits');
  await click('Keep edits and overwrite');
  assert.equal(sent[1].base_sha256, 'new');
});
test('Files keeps the dirty editor mounted when its last tree row disappears', async () => {
  const {default: Files} = await server.ssrLoadModule('/src/pages/Files.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  let deleted = false;
  globalThis.fetch = async (url) => new Response(JSON.stringify(
    url === '/api/files' ? {lean: deleted ? [] : [{path:'lean/A.lean',bytes:8,modified:1}], tex:[],cas:[],pdf:[]} :
    url.startsWith('/api/file?') ? {text:deleted ? '' : 'original', sha256:deleted ? null : 'old', missing:deleted} :
    url === '/api/environment' ? {checks:[]} : url === '/api/uploads' ? [] : {}));
  const page = async (revision) => act(async () => {
    root.render(React.createElement(SessionContext.Provider, {value:{revision,setDraft(){}}}, React.createElement(Files, {arg:'lean/A.lean'})));
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  await page(101);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  await type('my edits');
  deleted = true;
  await page(102);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  assert.ok(document.querySelector('textarea'), 'the editor stays mounted');
  assert.equal(buffer(), 'my edits');
  assert.match(document.body.textContent, /deleted on disk/);
});
test('environment reads revision changes and Re-probe explicitly refreshes', async () => {
  const {default: Environment} = await server.ssrLoadModule('/src/pages/Environment.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  const reads = [];
  globalThis.fetch = async (url) => { reads.push(url); return new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'available'}]})); };
  const page = async (revision) => act(async () => {
    root.render(React.createElement(SessionContext.Provider, {value:{revision}}, React.createElement(Environment)));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  await page(201);
  await page(202);
  assert.deepEqual(reads, ['/api/environment', '/api/environment']);
  await click('Re-probe');
  assert.equal(reads.at(-1), '/api/environment?refresh=1');
  globalThis.fetch = async (url) => { reads.push(url); return new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'changed config'}]})); };
  await page(203);
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  assert.match(document.body.textContent, /changed config/);
});
test('late save response does not erase a newer disk conflict', async () => {
  let finish;
  globalThis.fetch = async () => new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({ok:true,output:'saved',sha256:'saved'}))); });
  await type('submitted');
  await click('Save & check');
  await draw({text:'another write', sha256:'newer-disk'});
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(buffer(), 'submitted');
  assert.match(document.body.textContent, /changed on disk/);
});
test('save response for previous path never appears on a new file', async () => {
  let finish;
  globalThis.fetch = async () => new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({ok:true,output:'old-file-response',sha256:'saved'}))); });
  await type('submitted');
  await click('Save & check');
  await draw({path:'tex/Other.tex',kind:'tex',text:'other',sha256:'other'});
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(buffer(), 'other');
  assert.doesNotMatch(document.body.textContent, /old-file-response/);
});
test('a truncated conflict cannot be mistaken for deletion or overwritten', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({error:'changed on disk',conflict:true,text:'prefix',sha256:null,missing:false,truncated:true}), {status:409});
  await type('my edits');
  await click('Save & check');
  assert.equal(buffer(), 'my edits');
  assert.doesNotMatch(document.body.textContent, /deleted on disk|Keep edits and recreate/);
  assert.ok([...document.querySelectorAll('button')].find((b) => b.textContent === 'Keep edits and overwrite').disabled);
});
test('normalized save reload acknowledges the committed digest without conflicting with newer edits', async () => {
  let finish;
  globalThis.fetch = async () => new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({ok:true,output:'saved',sha256:'saved'}))); });
  await type('submitted');
  await click('Save & check');
  await type('newer edits');
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  await draw({text:'submitted\n',sha256:'saved'});
  assert.equal(buffer(), 'newer edits');
  assert.doesNotMatch(document.body.textContent, /changed on disk/);
});
test('switching Files paths never presents the previous file as the new buffer', async () => {
  const {default: Files} = await server.ssrLoadModule('/src/pages/Files.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  let finish;
  globalThis.fetch = async (url) => {
    if (url.includes('path=lean%2FB.lean')) return new Promise((resolve) => {
      finish = () => resolve(new Response(JSON.stringify({text:'second file',sha256:'second'})));
    });
    return new Response(JSON.stringify(
      url === '/api/files' ? {lean:[{path:'lean/A.lean',bytes:8,modified:1},{path:'lean/B.lean',bytes:8,modified:1}],tex:[],cas:[],pdf:[]} :
      url.startsWith('/api/file?') ? {text:'first file',sha256:'first'} :
      url === '/api/environment' ? {checks:[]} : url === '/api/uploads' ? [] : {}));
  };
  const page = async (path) => act(async () => {
    root.render(React.createElement(SessionContext.Provider, {value:{revision:401,setDraft(){}}}, React.createElement(Files, {arg:path})));
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  await page('lean/A.lean');
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
  assert.equal(buffer(), 'first file');
  await page('lean/B.lean');
  const showedPreviousBuffer = !!document.querySelector('textarea');
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 20)); });
  assert.equal(showedPreviousBuffer, false, 'wait for B rather than label A text as B');
  assert.equal(buffer(), 'second file');
});
test('explicit environment refresh survives leaving and reopening page', async () => {
  const {default: Environment} = await server.ssrLoadModule('/src/pages/Environment.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  const reads=[];
  globalThis.fetch=async(url)=>{reads.push(url);return new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:url.includes('refresh=1')?'new toolchain':'old toolchain'}]}));};
  const mount=async()=>act(async()=>{root.render(React.createElement(SessionContext.Provider,{value:{revision:9001}},React.createElement(Environment)));await new Promise(r=>setTimeout(r,20));});
  await mount(); await click('Re-probe');
  assert.match(document.body.textContent,/new toolchain/);
  await act(()=>root.render(null)); await mount();
  assert.match(document.body.textContent,/new toolchain/);
});
test('late environment re-probe cannot replace a newer configuration revision', async () => {
  const {default: Environment} = await server.ssrLoadModule('/src/pages/Environment.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  let finish;
  globalThis.fetch = async (url) => url.endsWith('?refresh=1')
    ? new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'old re-probe'}]}))); })
    : new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'current config'}]}));
  const page = async (revision) => act(async () => {
    root.render(React.createElement(SessionContext.Provider, {value:{revision}}, React.createElement(Environment)));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  await page(601);
  await click('Re-probe');
  await page(602);
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 10)); });
  await act(() => root.render(null));
  await page(602);
  assert.match(document.body.textContent, /current config/);
  assert.doesNotMatch(document.body.textContent, /old re-probe/);
});
for (const status of [200, 500]) {
test(`re-probe supersedes a pending canonical read (status ${status}) and survives remount`, async () => {
  const {default: Environment} = await server.ssrLoadModule('/src/pages/Environment.jsx');
  const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
  let finish;
  let normalReads = 0;
  globalThis.fetch = async (url) => {
    if (url.endsWith('?refresh=1')) return new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'new probe'}]}));
    if (++normalReads === 2) return new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'older read'}],error:'older read failure'}), {status})); });
    return new Response(JSON.stringify({checks:[{name:'lean',ok:true,detail:'initial read'}]}));
  };
  const page = async (revision) => act(async () => {
    root.render(React.createElement(SessionContext.Provider, {value:{revision}}, React.createElement(Environment)));
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
  await page(701);
  await page(702);
  await click('Re-probe');
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 10)); });
  assert.match(document.body.textContent, /new probe/);
  assert.doesNotMatch(document.body.textContent, /older read failure/);
  await act(() => root.render(null));
  await page(702);
  assert.match(document.body.textContent, /new probe/);
  assert.equal(normalReads, 2);
});
}

for (const action of ['Save & check', 'Check']) {
  test(`queued ${action} stays bound to loaded project after switch and unmount`, async () => {
    const {default: Files} = await server.ssrLoadModule('/src/pages/Files.jsx');
    const {SessionContext} = await server.ssrLoadModule('/src/session/SessionProvider.jsx');
    const disk = {'/projects/A':'original', '/projects/B':'original'};
    let active = '/projects/A';
    let deliver;
    const checked = [];
    globalThis.fetch = async (url, init) => {
      if (init?.method === 'PUT' || url === '/api/check') {
        const sent = JSON.parse(init.body);
        return new Promise((resolve) => { deliver = () => {
          assert.equal(sent.project, '/projects/A', 'identity comes from the loaded file, not live session state');
          if (sent.project !== undefined && sent.project !== active) {
            resolve(new Response(JSON.stringify({error:'Project changed; reopen the file.'}), {status:409}));
            return;
          }
          if (init.method === 'PUT') disk[active] = sent.source;
          else checked.push({project:active, source:sent.source});
          resolve(new Response(JSON.stringify({ok:true,output:'done',sha256:'saved'})));
        }; });
      }
      return new Response(JSON.stringify(
        url === '/api/files' ? {lean:[{path:'lean/A.lean',bytes:8,modified:1}],tex:[],cas:[],pdf:[]} :
        url.startsWith('/api/file?') ? {text:'original',sha256:'old',project:'/projects/A'} :
        url === '/api/environment' ? {checks:[]} : url === '/api/uploads' ? [] : {}));
    };
    await act(async () => {
      root.render(React.createElement(SessionContext.Provider, {value:{revision:action,setDraft(){},state:{path:'/projects/B'}}}, React.createElement(Files, {arg:'lean/A.lean'})));
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    await type('A private edits');
    await click(action);
    assert.equal(typeof deliver, 'function');
    active = '/projects/B';
    await act(() => root.render(null));
    await act(async () => { deliver(); await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.deepEqual(disk, {'/projects/A':'original','/projects/B':'original'});
    assert.deepEqual(checked, [], 'never check A source using B environment');
  });
}

test('same-path project change replaces dirty draft and ignores old-project response', async () => {
  let finish;
  globalThis.fetch = async () => new Promise((resolve) => { finish = () => resolve(new Response(JSON.stringify({ok:true,output:'old-project-response',sha256:'saved'}))); });
  await draw({project:'/projects/A'});
  await type('A edits');
  await click('Save & check');
  await draw({project:'/projects/B',text:'B original',sha256:'B hash'});
  await act(async () => { finish(); await new Promise((resolve) => setTimeout(resolve, 0)); });
  assert.equal(buffer(), 'B original');
  assert.doesNotMatch(document.body.textContent, /old-project-response|changed on disk/);
});
