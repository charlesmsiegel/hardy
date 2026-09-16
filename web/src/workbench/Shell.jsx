// The frame every workbench page lives inside: a four-row grid (top bar, tab
// bar, body, footer strip) with the body itself splitting into the active
// page and the chat dock when the dock is pinned and the page is not Chat.
//
// Two mounting rules hold at once here, and they pull in opposite
// directions on purpose. Every page but Chat is mounted only while its tab
// is the active route -- `Panels.jsx` in the old client named why: a hidden
// panel has no effect running, so opening one is a mount and a mount is a
// fetch, without a line of code saying so. Chat is the deliberate exception:
// it is built once, below, and handed to `Dock` to place, so switching tabs
// or cycling the dock never refetches its transcript. `PAGES[route.page]`
// is a lookup rather than a switch for the same reason `pages/index.js`
// gives -- a tab three tasks have not reached yet should draw the same
// honest "not built yet" as one that legitimately has nothing to show,
// never a blank panel that looks like a bug.

import {useCallback, useState} from 'react';
import Empty from '../components/Empty.jsx';
import useHash from '../session/useHash.js';
import useSession from '../session/useSession.js';
import {PAGES} from '../pages/index.js';
import Dock from './Dock.jsx';
import DropOverlay from './DropOverlay.jsx';
import Footer from './Footer.jsx';
import Peek from './Peek.jsx';
import TabBar from './TabBar.jsx';
import TopBar from './TopBar.jsx';

function notBuilt(page) {
  return <Empty title="not built yet" line={`The ${page} page has not landed yet.`} />;
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
    status, runningTool, prompts, refusal, projects, setProjects, refreshProjects, revision,
  } = useSession();
  //: Chrome state, not session state -- what the user last did with the dock
  //: is not something a reload or another tab needs to agree with, so it
  //: lives here rather than in the hash or the server. `pinned` first, same
  //: as the prototype's own default.
  const [dock, setDock] = useState('pinned');
  //: The peek popover, or `null` when none is open. Held here, not in a
  //: page, because a page is unmounted the moment the route moves off it --
  //: the prototype closes the popover on navigation for the same reason
  //: `Shell` would lose it anyway, so nothing is lost by owning it at this
  //: level instead.
  const [peek, setPeek] = useState(null);
  const openPeek = useCallback((info) => setPeek(info), []);
  const closePeek = useCallback(() => setPeek(null), []);

  const isChat = route.page === 'chat';
  // Tree (Task 13a) is reached only through the toggle in Chat's own header
  // and shares its full-width, no-dock-aside layout -- it is a view of the
  // same chat, not a separate page the dock should sit beside. `isChat`
  // alone still picks which mount holds `<ChatPage/>` (below): Tree is its
  // own registered page, drawn through `pageContent`, not through the dock.
  const isTree = route.page === 'tree';
  const ChatPage = PAGES.chat;
  const chatContent = ChatPage ? <ChatPage arg={route.arg} onPeek={openPeek} /> : notBuilt('chat');

  const Page = isChat ? null : PAGES[route.page];
  // `null`, not omitted: the slot below tests this directly, and skipping
  // the branch when `isChat` is what keeps Chat from being asked for twice.
  const pageContent = isChat ? null : Page ? <Page arg={route.arg} onPeek={openPeek} /> : notBuilt(route.page);

  const showAside = dock === 'pinned' && !isChat && !isTree;
  const bodyCols = showAside ? 'minmax(0,1fr) 380px' : 'minmax(0,1fr)';

  const running = runningLabel(status, runningTool);
  const waiting = prompts.length ? `${prompts.length} card${prompts.length === 1 ? '' : 's'}` : '';

  return (
    <div className="wb-shell">
      <TopBar
        status={status}
        runningTool={runningTool}
        route={route}
        go={go}
        projects={projects}
        setProjects={setProjects}
        refreshProjects={refreshProjects}
        revision={revision}
      />
      <TabBar route={route} go={go} dock={dock} onToggleDock={setDock} />
      <div className="wb-split" style={{gridTemplateColumns: bodyCols}}>
        {pageContent !== null ? <div className="wb-page">{pageContent}</div> : null}
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
      {peek ? <Peek info={peek} onClose={closePeek} go={go} /> : null}
    </div>
  );
}
