import {useEffect, useRef, useState} from 'react';
import {post} from '../api.js';

export const exact = (record) => record?.ref || (record?.id && record?.digest ? {id:record.id,digest:record.digest} : null);
export const selector = (record) => { const ref = exact(record); return ref ? `${ref.id}@${ref.digest}` : ''; };
export const fromSelector = (value) => {
  if (!value) return null;
  const split = value.lastIndexOf('@');
  return {id:value.slice(0,split),digest:value.slice(split+1)};
};
export const formValues = (form) => Object.fromEntries(new window.FormData(form));
export const optionalRef = (value) => value ? fromSelector(value) : null;
export const lines = (text) => String(text || '').split('\n').map((line) => line.trim()).filter(Boolean);
export const sourceChoiceKey = ({path,digest,artifact,representation,retained}) => JSON.stringify({path,digest,artifact:artifact || null,representation:representation || null,retained:retained || null});
export const evidenceChoiceKey = (reference) => JSON.stringify(reference);

export function actionFeedback(record) {
  if (typeof record === 'string') return `Saved: ${record}`;
  const outcome=record?.status ? record : record?.receipt || record?.proposal;
  if (outcome?.status) {
    const label=String(outcome.status).replace(/^./,(letter) => letter.toUpperCase());
    return `${label}${outcome.detail ? ` — ${outcome.detail}` : ''}`;
  }
  return 'Recorded.';
}

export function useProjectAction(project, selection, onDone) {
  const identity = `${project || ''}:${selection || ''}`;
  const current = useRef(identity);
  const generation = useRef(0);
  const pendingRequest = useRef(null);
  if (current.current !== identity) {current.current=identity;generation.current++;pendingRequest.current=null;}
  const [pending,setPending] = useState(false);
  const [message,setMessage] = useState('');
  const [error,setError] = useState('');
  const [result,setResult] = useState(null);
  const invalidate = () => {generation.current++;setMessage('');setError('');setResult(null);};
  useEffect(() => {setPending(false);setMessage('');setError('');setResult(null);},[identity]);
  useEffect(() => () => {generation.current++;pendingRequest.current=null;},[]);
  async function submit(operation, fields={}, nested=true) {
    const started = identity;
    const token = ++generation.current;
    pendingRequest.current=token;
    setPending(true);setError('');setMessage('');
    const active = () => current.current === started && generation.current === token;
    try {
      const response = await post('/api/project',{operation,project,...(nested ? {request:fields} : fields)});
      if (!active()) return null;
      setResult(response.record);
      setMessage(actionFeedback(response.record));
      onDone?.(response.record);
      return response.record;
    } catch (failure) {
      if (active()) setError(failure.message);
      return null;
    } finally {
      // Only this request can clear its busy state. Editing the form may
      // invalidate its result without changing who owns the pending work.
      // Returning A -> B -> A must not let old A clear a newer A request.
      if (current.current === started && pendingRequest.current === token) {
        pendingRequest.current=null;
        setPending(false);
      }
    }
  }
  return {submit,pending,message,error,result,invalidate};
}

export const OBLIGATION_STATES = ['open','investigating','blocked','resolved','dismissed','abandoned'];
export function filterObligations(values, status='outstanding') {
  const outstanding = new Set(['open','investigating','blocked']);
  return values.filter((value) => status === 'all' || (status === 'outstanding' ? outstanding.has(value.status) : value.status === status));
}
