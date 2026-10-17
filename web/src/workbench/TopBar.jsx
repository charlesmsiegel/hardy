// The frame's top row: the wordmark, the project switcher, the running-turn
// indicator and the slot for the session's stats. Status, Environment and
// Help used to be links here; they are entries in the sidebar's Session
// group now, so every page is reached the same way.

import Absent from '../components/Absent.jsx';
import ProjectSwitcher from './ProjectSwitcher.jsx';

export default function TopBar({status, runningTool, projects, setProjects, refreshProjects, revision}) {
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
      {/* Tokens, cost, checks-leased and active time are real fields the
          prototype reserves this space for, but no endpoint answers them yet
          in this shipment -- Absent says so rather than a fabricated number. */}
      <span className="wb-stats" title="tokens this chat · cost · official checks leased of ceiling · active time">
        <Absent kind="unreported" />
      </span>
    </div>
  );
}
