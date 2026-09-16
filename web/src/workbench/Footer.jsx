// The body row's `auto` footer: the dock collapsed to a status strip, or
// forced there below 900px regardless of what the dock toggle last chose --
// a 380px aside does not fit a phone, but a way back to the running chat
// still has to.
//
// `Shell` does not render this at all on the Chat route: the strip only ever
// points at the dock's chat, and on that route the chat is already the whole
// page, so there is nothing for the strip to summarise. That is a plain
// conditional mount, not the "same instance" rule Dock has to keep -- this
// component holds no transcript of its own, only a read of the session on
// every render, so mounting and unmounting it costs nothing.

import Absent from '../components/Absent.jsx';

export default function Footer({dock, chatLabel, running, queued, waiting, refusal, model, go}) {
  return (
    <div className="wb-strip" style={{display: dock === 'strip' ? 'flex' : 'none'}}>
      <strong>{chatLabel}</strong>
      {running ? (
        <span className="wb-strip__running">
          <span className="wb-turn__dot" />
          {running}
        </span>
      ) : null}
      {waiting ? <span className="wb-strip__waiting">{waiting} waiting</span> : null}
      {queued ? <span className="wb-strip__queued">{queued} queued</span> : null}
      {refusal ? <span className="wb-strip__refusal">{refusal}</span> : null}
      <span className="wb-strip__stats">{model || <Absent kind="unreported" />}</span>
      <a
        className="wb-strip__open"
        href="#/chat"
        onClick={(event) => {
          event.preventDefault();
          go('chat');
        }}
      >
        {'open chat ↗'}
      </a>
    </div>
  );
}
