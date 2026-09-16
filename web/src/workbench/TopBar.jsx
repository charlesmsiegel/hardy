// The frame's top row: the wordmark, the running-turn indicator, and the
// three routes (`Status`, `Environment`, `Help`) that live beside the tab bar
// rather than inside it because they describe the session, not a body of
// work the way the other ten tabs do.
//
// The project switcher itself (the dropdown behind the slug button) is
// Task 11's; this row prints the current slug as plain text so the layout is
// final now and Task 11 only has to swap the plain text for the button.

import Absent from '../components/Absent.jsx';

export default function TopBar({status, runningTool, route, go}) {
  const busy = status.turn_running || status.command_running;

  const link = (page, label) => (
    <a
      href={`#/${page}`}
      onClick={(event) => {
        event.preventDefault();
        go(page);
      }}
      className={route.page === page ? 'wb-toplink wb-toplink--on' : 'wb-toplink'}
    >
      {label}
    </a>
  );

  return (
    <div className="wb-topbar">
      <span className="wb-wordmark">Hardy</span>
      <span className="wb-root">~/math</span>
      <span className="wb-project">{status.slug || <Absent kind="unreported" />}</span>
      {busy ? (
        <span className="wb-turn">
          <span className="wb-turn__dot" />
          {status.turn_running ? 'turn running' : 'running'}
          {runningTool ? ` · ${runningTool}` : ''}
          {status.queued ? ` · ${status.queued} queued` : ''}
        </span>
      ) : null}
      {/* Tokens, cost, checks-leased and active time are real fields the
          prototype reserves this space for, but no endpoint answers them yet
          in this shipment -- Absent says so rather than a fabricated number. */}
      <span className="wb-stats" title="tokens this chat · cost · official checks leased of ceiling · active time">
        <Absent kind="unreported" />
      </span>
      <span className="wb-toplinks">
        {link('status', 'Status')}
        {link('environment', 'Environment')}
        {link('help', 'Help')}
      </span>
    </div>
  );
}
