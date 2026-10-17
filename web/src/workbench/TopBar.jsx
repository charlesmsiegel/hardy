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
//
// A second cancel is not a repeat of the first: the server's first one asks
// the run to stop, and a second one kills the processes that did not, losing
// a computer algebra kernel's state. Esc is two deliberate presses; a button
// invites a double click, so Stop is one press and then a different button.

import {useEffect, useState} from 'react';
import Absent from '../components/Absent.jsx';
import ProjectSwitcher from './ProjectSwitcher.jsx';

/** How long a stop is given before the button offers to force it. */
export const FORCE_AFTER_MS = 3000;

//: What the button says and does in each phase of one run's stop. The phase
//: is state of the button, not of the session: it exists only while the run
//: does (the button is not drawn otherwise), so a later run starts at
//: `ready` without anything having to reset it.
const STOP = {
  ready: {
    label: 'Stop',
    title: 'Stop what is running (the same as Esc).',
  },
  stopping: {
    label: 'Stopping…',
    title: 'Asked it to stop. If it is still running in a few seconds, this offers to force the stop.',
  },
  force: {
    label: 'Force stop',
    title: 'Kills the running processes that did not stop. A computer algebra kernel among them loses its state.',
  },
  forced: {
    label: 'Force stopping…',
    title: 'Asked to kill what had not stopped.',
  },
};

function StopButton({onStop, forceAfterMs}) {
  const [phase, setPhase] = useState('ready');
  // The offer to force is made once, a set time after the first press; a run
  // that ends first unmounts the button and takes the timer with it.
  useEffect(() => {
    if (phase !== 'stopping') return undefined;
    const timer = setTimeout(() => setPhase('force'), forceAfterMs);
    return () => clearTimeout(timer);
  }, [phase, forceAfterMs]);

  const press = () => {
    // `ready` sends the first cancel, `force` the second; the other phases
    // are disabled, so a double click reaches neither.
    setPhase(phase === 'ready' ? 'stopping' : 'forced');
    onStop();
  };

  return (
    <button
      type="button"
      className="button button--danger button--small"
      title={STOP[phase].title}
      disabled={phase === 'stopping' || phase === 'forced'}
      onClick={press}
    >
      {STOP[phase].label}
    </button>
  );
}

export default function TopBar({
  status, runningTool, projects, setProjects, refreshProjects, revision, waiting, onStop, go, forceAfterMs = FORCE_AFTER_MS,
}) {
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
      {busy ? <StopButton onStop={onStop} forceAfterMs={forceAfterMs} /> : null}
      {waiting ? (
        <button
          type="button"
          className="button button--yes button--small"
          title="Cards in the chat are waiting for an answer. Open the chat."
          aria-label={`${waiting} waiting for an answer — open Chat`}
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
