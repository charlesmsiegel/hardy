// The frame every workbench page lives inside: a grid of the top bar across
// the top, the sidebar down the left, and beside it the body over the footer
// strip -- the body itself splitting into the active page and the chat dock
// when the dock is pinned and the page is not Chat.
//
// Two mounting rules hold at once here, and they pull in opposite
// directions on purpose. Every page but Chat is mounted only while it is
// the active route -- `Panels.jsx` in the old client named why: a hidden
// panel has no effect running, so opening one is a mount and a mount is a
// fetch, without a line of code saying so. Chat is the deliberate exception:
// its one element is handed to `Dock` to place, so switching pages or cycling
// the dock keeps its mount. SessionProvider fetches the transcript on first
// load and when the status's slug/chat pair changes; `changed` refreshes the
// projects and revision-keyed panels. `PAGES[route.page]` is a lookup rather
// than a switch: an unregistered route draws "unknown route", distinct from
// a registered page with nothing to show.
//
// Each page body has its own error boundary, including Landing. Chat has a
// separate boundary under Dock's stable body: another page's exception leaves
// its draft mounted, and a Chat exception stays inside the dock. The sidebar,
// one top bar and footer strip sit outside both, so navigation and Stop still
// work. A new project, page or sub-route retries a failed page; Chat retries
// on project/chat changes instead of on navigation around it.
// The palette belongs to the frame too: its scope ends on navigation or a
// project switch. Choosing a command composes a local draft; only the
// composer's explicit send enters the session's input path.

import {useCallback, useEffect, useRef, useState} from 'react';
import Empty from '../components/Empty.jsx';
import ErrorBoundary from '../components/ErrorBoundary.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';
import {PAGES} from '../pages/index.js';
import Dock from './Dock.jsx';
import DropOverlay from './DropOverlay.jsx';
import Footer from './Footer.jsx';
import Landing from './Landing.jsx';
import Peek from './Peek.jsx';
import Sidebar from './Sidebar.jsx';
import TopBar from './TopBar.jsx';
import CommandPalette from './CommandPalette.jsx';
import {NAV_GROUPS} from './navigation.js';
import {paletteEntries} from './palette.js';
import {WORKFLOW_FORMS} from './workflowForms.js';

// Every sidebar entry the app draws has a page in `PAGES`, so the only route
// that can miss is one nothing ever asked for -- a stray hash, a typo, a link
// that used to exist. That is honestly "not a route at all", and
// `unknownRoute` says exactly that.
//
// There used to be a `PLANNED_NOT_BUILT` list beside this and a `notBuilt`
// fallback that said "the <name> page has not landed yet". It emptied when
// Status landed, and it is gone rather than left empty: a fallback that can
// no longer fire is a claim waiting to be made about the wrong input, and
// `#/xyzzy` asserting that a page was planned was issue #170's third item.

function unknownRoute(page) {
  return <Empty title="unknown route" line={`#/${page} is not a route Hardy has.`} />;
}

/** `true`/`command_running` reduced to the one phrase every dock/strip/top-bar
 *  reading of it prints, so the three places agree without repeating the
 *  branch three times. */
function runningLabel(status, runningTool) {
  if (!status.turn_running && !status.command_running) return '';
  const base = status.turn_running ? 'turn running' : 'running';
  return runningTool ? `${base} · ${runningTool}` : base;
}

export default function Shell() {
  const [route, go] = useHash();
  const {
    status, runningTool, prompts, refusal, projects, setProjects, refreshProjects, revision, loaded, messages, cancel,
    commands, compose, composeFocus = 0,
  } = useSession();
  //: What the first load said if it failed: `failed` files it into the
  //: transcript, which the unloaded shell does not draw, so it is repeated
  //: here rather than leaving "Connecting…" up forever with the reason hidden.
  const loadError = loaded ? null : messages.filter((m) => m.kind === 'system' && m.style === 'error').at(-1);
  //: Chrome state, not session state -- what the user last did with the dock
  //: is not something a reload or another tab needs to agree with, so it
  //: lives here rather than in the hash or the server. `pinned` first, same
  //: as the prototype's own default.
  const [dock, setDock] = useState('pinned');
  //: Whether the user has narrowed the sidebar to its rail of glyphs. Chrome
  //: state for the same reason as `dock`; below 1100px the rail is forced by
  //: `styles.css` whatever this says, and this is what returns when the
  //: window widens again.
  const [collapsed, setCollapsed] = useState(false);
  //: Opening is chrome state; navigation or a project switch closes it so
  //: an old project's entry cannot be chosen against the new session.
  const [paletteOpen, setPaletteOpen] = useState(false);
  useEffect(() => {
    const onKey = (event) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 'k') return;
      event.preventDefault();
      if (!event.repeat) setPaletteOpen((open) => !open);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, []);
  useEffect(() => setPaletteOpen(false), [route.page, route.arg, status.path]);
  const lastCompose = useRef(composeFocus);
  useEffect(() => {
    if (lastCompose.current === composeFocus) return;
    lastCompose.current = composeFocus;
    // Tree and the narrow layout also hide a pinned dock. A draft needs a
    // visible composer in those shapes just as it does in strip/hidden mode.
    const narrow = window.matchMedia?.('(max-width: 900px)').matches;
    if (route.page !== 'chat' && (dock !== 'pinned' || route.page === 'tree' || narrow)) go('chat');
  }, [composeFocus, dock, route.page, go]);
  //: Nothing open: the server started with no registered project, or the
  //: user closed the one that was. `false`, not falsy -- the status is
  //: `EMPTY_STATUS` before the first load, and that must draw the loading
  //: shell rather than claim there is no project. Every project-scoped
  //: endpoint answers 409 in this state, so the jobs panel is not asked.
  const empty = status.open === false;
  // Nor before the first load has said whether anything is open: mounting
  // the pages then asked six project-scoped endpoints that all answered 409
  // for the instant before the project menu replaced them.
  const settled = loaded && !empty;
  //: The Jobs entry's attention count in the sidebar: one small panel read
  //: here rather than inside `Sidebar.jsx`, which stays a stateless list of
  //: buttons.
  //: Reading it at the shell costs one request per `revision`, the same as
  //: any other panel, and does not touch the Chat/Tree mounting rules above.
  const jobsPanel = usePanel(settled ? '/api/jobs' : null, revision);
  const jobsAttention = jobsPanel.data?.attention?.length || 0;
  // Selectable chat ids belong to the project that answered, not whichever
  // project is current when the palette is reopened during a refetch.
  const chatsPanel = usePanel(settled ? '/api/chats' : null, revision, status.path);
  const palette = (
    <CommandPalette
      open={paletteOpen}
      onClose={() => setPaletteOpen(false)}
      entries={settled ? paletteEntries({
        pages: NAV_GROUPS.flatMap((group) => group.items
          .filter((item) => PAGES[item.page])
          .map((item) => ({...item, detail: group.label}))),
        commands, chats: chatsPanel.data || [], forms: WORKFLOW_FORMS,
      }) : []}
      onRun={(run) => {
        setPaletteOpen(false);
        if (run.compose !== undefined) compose(run.compose);
        else go(run);
      }}
    />
  );
  //: The peek popover, or `null` when none is open. Held here, not in a
  //: page, because a page is unmounted the moment the route moves off it --
  //: the prototype closes the popover on navigation for the same reason
  //: `Shell` would lose it anyway, so nothing is lost by owning it at this
  //: level instead.
  const [peek, setPeek] = useState(null);
  const openPeek = useCallback((info) => setPeek(info), []);
  const closePeek = useCallback(() => setPeek(null), []);

  const isChat = route.page === 'chat';
  // Tree (Task 13a) has its own sidebar entry beside the toggle in Chat's
  // own header, and shares Chat's full-width, no-dock-aside layout -- it is
  // a view of the same chat, not a separate page the dock should sit
  // beside. `isChat` alone still picks which mount holds `<ChatPage/>`
  // (below): Tree is its own registered page, drawn through `pageContent`,
  // not through the dock.
  const isTree = route.page === 'tree';
  const ChatPage = PAGES.chat;
  const chatContent = (
    <ErrorBoundary resetKey={`${status.path}/chat/${status.chat}`}>
      {ChatPage ? <ChatPage arg={route.arg} onPeek={openPeek} /> : unknownRoute('chat')}
    </ErrorBoundary>
  );

  const Page = isChat ? null : PAGES[route.page];
  // `null`, not omitted: the slot below tests this directly, and skipping
  // the branch when `isChat` is what keeps Chat from being asked for twice.
  //
  // `key` carries the project. Without it React reuses the mounted page
  // across a project switch, and page-local state goes with it -- including
  // a pending confirmation card, which holds the COMPLETE command string it
  // would submit. A confirm opened in Jobs, Publications or Checkpoints and
  // answered after switching would send that old command to the new
  // session, and ids recur across projects (`d-01`, `Main`, `scope`), so the
  // result is not a harmless refusal but the right command against the wrong
  // project's object.
  //
  // Deliberately not applied to Chat: the dock is mounted once and kept
  // across route changes on purpose, and SessionProvider reloads the
  // transcript when the status's slug/chat pair changes. `changed` refreshes
  // the projects and revision-keyed panel reads, not the transcript.
  const pageContent = isChat
    ? null
    : Page
      ? <Page key={`${status.path}/${route.page}`} arg={route.arg} onPeek={openPeek} />
      : unknownRoute(route.page);

  // One sidebar for all three shapes below. Disabled while nothing is open
  // (still loading, or no project), which also drops the counts and the dock
  // toggle; `collapsed` holds in every shape, so opening a project does not
  // move the rail.
  const sidebar = (disabled) => (
    <Sidebar
      route={route}
      go={go}
      badges={{jobs: jobsAttention, chat: prompts.length}}
      collapsed={collapsed}
      onToggleCollapsed={setCollapsed}
      dock={dock}
      onToggleDock={setDock}
      disabled={disabled}
    />
  );

  // One top bar for all three shapes below, for the reason `sidebar` is one:
  // a prop dropped at one of three copies would be a control that works
  // everywhere but the shape nobody tested.
  const topBar = (
    <TopBar
      status={status}
      runningTool={runningTool}
      projects={projects}
      setProjects={setProjects}
      refreshProjects={refreshProjects}
      revision={revision}
      waiting={prompts.length}
      onStop={cancel}
      go={go}
    />
  );

  if (!loaded) {
    return (
      <div className="wb-shell">
        {topBar}
        {sidebar(true)}
        {palette}
        <div className="wb-split" style={{gridTemplateColumns: 'minmax(0,1fr)'}}>
          <div className="wb-page">
            {loadError
              ? <p className="panel__error">{loadError.text}</p>
              : <p className="panel__note">Connecting…</p>}
          </div>
        </div>
      </div>
    );
  }

  if (empty) {
    return (
      <div className="wb-shell">
        {topBar}
        {sidebar(true)}
        {palette}
        <div className="wb-split" style={{gridTemplateColumns: 'minmax(0,1fr)'}}>
          <div className="wb-page">
            <ErrorBoundary resetKey={`${status.path}/${route.page}/${route.arg}`}>
              <Landing status={status} projects={projects} setProjects={setProjects} refreshProjects={refreshProjects} />
            </ErrorBoundary>
          </div>
        </div>
      </div>
    );
  }

  const showAside = dock === 'pinned' && !isChat && !isTree;
  const bodyCols = showAside ? 'minmax(0,1fr) 380px' : 'minmax(0,1fr)';

  const running = runningLabel(status, runningTool);
  const waiting = prompts.length ? `${prompts.length} card${prompts.length === 1 ? '' : 's'}` : '';

  return (
    <div className="wb-shell">
      {topBar}
      {sidebar(false)}
      <div className="wb-split" style={{gridTemplateColumns: bodyCols}}>
        {pageContent !== null ? (
          <div className="wb-page">
            <ErrorBoundary resetKey={`${status.path}/${route.page}/${route.arg}`}>
              {pageContent}
            </ErrorBoundary>
          </div>
        ) : null}
        <Dock
          isChatPage={isChat}
          hidden={isTree}
          dock={dock}
          chatLabel={status.chat || 'chat'}
          running={running}
          queued={status.queued || 0}
          content={chatContent}
          go={go}
        />
      </div>
      {isChat || isTree ? null : (
        <Footer
          dock={dock}
          chatLabel={status.chat || 'chat'}
          running={running}
          queued={status.queued || 0}
          waiting={waiting}
          refusal={refusal}
          model={status.model}
          go={go}
        />
      )}
      <DropOverlay go={go} />
      {palette}
      {peek ? <Peek info={peek} onClose={closePeek} go={go} /> : null}
    </div>
  );
}
