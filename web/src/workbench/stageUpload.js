// Both file selection and dropping use the same digest, Lean scan and
// per-file refresh signal. A picker can retire its owner before the upload;
// an accepted request itself remains governed by the existing server API.
import {upload} from '../api.js';
import {record} from './uploadScans.js';

export async function stageOne(file, isCurrent = () => true) {
  const bytes = new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()));
  const sha256 = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
  let scan = null;
  if (file.name.toLowerCase().endsWith('.lean')) {
    try {
      const text = await file.text();
      const names = [...text.matchAll(/\b(?:theorem|lemma|def|axiom)\s+([A-Za-z_][A-Za-z0-9_'.]*)/g)].map(match => match[1]);
      scan = {names, sorry: /\bsorry\b/.test(text)};
    } catch {
      // An unreadable scan does not prevent staging the original file.
    }
  }
  if (!isCurrent()) return;
  const staged = await upload(file);
  if (!isCurrent()) return;
  record(staged.name, {sha256, scan});
  window.dispatchEvent(new window.CustomEvent('hardy:staged', {detail:{name:staged.name}}));
}
