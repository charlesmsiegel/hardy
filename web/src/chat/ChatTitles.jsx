// Titles are metadata writes, followed by fresh server reads. The keyed
// parent owns a project/chat pair; unmounting drops its local reply, so a
// delayed create can never navigate another project or rename another chat.
import {useEffect, useRef, useState} from 'react';
import {patch, post} from '../api.js';
import {Field} from '../workbench/ProjectWorkflow.jsx';

function TitleForm({kind, title='', disabled, save, onSaved}) {
  const [value, setValue] = useState(title);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const live = useRef(true);
  const submitting = useRef(false);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);
  const create = kind === 'new';
  return <form className="wb-chat-title" data-chat-action={kind} onSubmit={async (event) => {
    event.preventDefault();
    if (disabled || submitting.current || !value.trim()) return;
    submitting.current = true;
    setPending(true);setError('');
    try {
      const result = await save(value.trim());
      if (!live.current) return;
      await onSaved(result, () => live.current);
    } catch (failure) {
      if (live.current) setError(String(failure?.message ?? failure));
    } finally {
      if (live.current) {submitting.current=false;setPending(false);}
    }
  }}>
    <fieldset className="wb-fieldset" disabled={disabled || pending}>
      <Field label={create ? 'New chat title' : 'Rename chat title'} name="title" initial={title}
        onChange={(event) => {setValue(event.target.value);setError('');}}/>
      <button className="button button--small" type="submit">{pending ? 'Saving…' : create ? 'New chat' : 'Rename chat'}</button>
    </fieldset>
    {error ? <p className="wb-chat-rail__error" role="alert">{error}</p> : null}
  </form>;
}

export default function ChatTitles({slug, chat, currentChat, disabled, onRefresh, onOpen}) {
  const path = `/api/projects/${encodeURIComponent(slug)}/chats`;
  return <div className="wb-chat-titles">
    <TitleForm kind="new" disabled={disabled} save={(title) => post(path, {title})}
      onSaved={async (created, active) => {
        await onRefresh();
        if (active()) onOpen(created.id);
      }}/>
    {currentChat ? <TitleForm key={chat} kind="rename" title={currentChat.title || chat} disabled={disabled || chat === 'main'}
      save={(title) => patch(`${path}/${encodeURIComponent(chat)}`, {title})} onSaved={onRefresh}/>
      : <p className="panel__note">Reading the current chat title…</p>}
    {chat === 'main' ? <p className="panel__note">The main chat cannot be renamed.</p> : null}
  </div>;
}
