// Chat and Tree share counts and the transcript/tree toggle. Chat also
// supplies the session model command: its picker reflects reported state,
// and its project/chat key discards a typed identity when ownership changes.
import ModelPicker from './ModelPicker.jsx';

export default function Header({chatLabel, running, entries, lines, active, go, model, busy, revision, onSend, owner, chat}) {
  return (
    <div className="wb-chat-header">
      <strong>{chatLabel}</strong>
      {running ? <span>· {running}</span> : null}
      <span>
        · {entries} entries · {lines} lines
      </span>
      {onSend ? <ModelPicker key={`${owner}/${chat}`} owner={owner} model={model} busy={busy} revision={revision} onSend={onSend}/> : null}
      <span className="wb-chat-header__toggle">
        <button
          type="button"
          className={active !== 'tree' ? 'wb-viewtoggle wb-viewtoggle--on' : 'wb-viewtoggle'}
          onClick={() => go('chat')}
        >
          transcript
        </button>
        <button
          type="button"
          className={active === 'tree' ? 'wb-viewtoggle wb-viewtoggle--on' : 'wb-viewtoggle'}
          onClick={() => go('tree')}
        >
          tree
        </button>
      </span>
    </div>
  );
}
