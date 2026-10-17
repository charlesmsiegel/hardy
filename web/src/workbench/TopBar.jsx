// The frame's top row: the wordmark, the project switcher, the running-turn
// indicator with its Stop button, the count of cards waiting for an answer,
// and the slot for the session's stats. Status, Environment and Help used to
// be links here; they are entries in the sidebar's Session group now, so
// every page is reached the same way.
//
// Stop and the waiting count live in the top bar because it is the one row
// drawn on every page. Stopping a turn used to need Esc with the composer
// focused, and an approval card could sit in a dock the user had hidden or on
// a page that is not Chat. Both are sugar over paths that already exist --
// `onStop` is the session's `cancel()`, the `POST /api/cancel` Esc makes, and
// the count opens the chat the cards are in -- so neither adds a second way
// to do anything. The session's own functions arrive as props, like the rest
// of what `Shell` reads from `useSession()`, which keeps this component a
// plain view of them.

import Absent from '../components/Absent.jsx';
import ProjectSwitcher from './ProjectSwitcher.jsx';

export default function TopBar({status, runningTool, projects, setProjects, refreshProjects, revision, waiting, onStop, go}) {
  const busy = status.turn_running || status.command_running;

  return (
    <div className="wb-topbar">
      <span className="wb-wordmark">Hardy</span>
      {/* The open project's root, which is a real fact about where its
          files are; nothing while nothing is open. */}
      {status.open ? <span className="wb-root" title={status.path}>{status.root}</span> : null}
      <ProjectSwitcher
        status={status}
        projects={projects}
        setProjects={setProjects}
        refreshProjects={refreshProjects}
        revision={revision}
      />
      {busy ? (
        <span className="wb-turn">
          <span className="wb-turn__dot" />
          {status.turn_running ? 'turn running' : 'running'}
          {runningTool ? ` · ${runningTool}` : ''}
          {status.queued ? ` · ${status.queued} queued` : ''}
        </span>
      ) : null}
      {/* The same press as Esc, so it also does what a second Esc does: the
          title says so, because a second click is not a harmless repeat. */}
      {busy ? (
        <button
          type="button"
          className="button button--danger button--small"
          title="Stop what is running (the same as Esc). Pressed again, it kills what has not stopped."
          onClick={onStop}
        >
          Stop
        </button>
      ) : null}
      {waiting ? (
        <button
          type="button"
          className="button button--yes button--small"
          title="Cards in the chat are waiting for an answer. Open the chat."
          onClick={() => go('chat')}
        >
          {waiting} waiting
        </button>
      ) : null}
      {/* Tokens, cost, checks-leased and active time are real fields the
          prototype reserves this space for, but no endpoint answers them yet
          in this shipment -- Absent says so rather than a fabricated number. */}
      <span className="wb-stats" title="tokens this chat · cost · official checks leased of ceiling · active time">
        <Absent kind="unreported" />
      </span>
    </div>
  );
}
