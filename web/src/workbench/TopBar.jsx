// The frame's top row: the wordmark, the project switcher, the running-turn
// indicator, and the three routes (`Status`, `Environment`, `Help`) that live
// beside the tab bar rather than inside it because they describe the
// session, not a body of work the way the other ten tabs do.

import Absent from '../components/Absent.jsx';
import ProjectSwitcher from './ProjectSwitcher.jsx';

export default function TopBar({status, runningTool, route, go, projects, setProjects, refreshProjects, revision}) {
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
      <span className="wb-toplinks">
        {link('status', 'Status')}
        {link('environment', 'Environment')}
        {link('help', 'Help')}
      </span>
    </div>
  );
}
