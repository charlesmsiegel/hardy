// Chat: the transcript surface. Mounted once by `Shell` regardless of the
// active tab (see `workbench/Dock.jsx`), and shown full width -- rail,
// header, transcript, composer -- only when the route is actually `chat` or
// `tree`; docked beside another page it draws just the header, transcript
// and composer, because a 200px rail would eat half of a 380px dock.
//
// Every fact the left rail and header print is sourced from a real endpoint
// or computed from one; nothing here is invented to fill the prototype's
// layout. Three figures the prototype shows that nothing this shipment
// serves can answer honestly: per-chat tokens and cost (`/api/jobs`'s usage
// is session-wide, already labelled "all branches" on Home, and reusing it
// here under a per-chat label would be exactly the kind of merge the design
// forbids), and which backend "thread" state (fresh/resumed) this chat is
// in. All three print `Absent`.

import {useEffect, useMemo, useState} from 'react';
import {post} from '../api.js';
import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Composer from '../chat/Composer.jsx';
import Header from '../chat/Header.jsx';
import Transcript from '../chat/Transcript.jsx';
import {headerStats, isAbandon, isFork} from '../chat/tree.js';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

export default function Chat({onPeek}) {
  const {
    status, commands, messages, prompts, draft, refusal, runningTool, revision, loaded,
    send, answer, cancel, setDraft, blocked,
  } = useSession();
  const [route, go] = useHash();
  const fullWidth = route.page === 'chat' || route.page === 'tree';

  const summary = usePanel('/api/summary', revision);
  const chats = usePanel('/api/chats', revision);
  const tree = usePanel('/api/tree', revision);
  const jobs = usePanel('/api/jobs', revision);
  const models = usePanel('/api/models', revision);
  const graph = usePanel('/api/graph', revision);

  const busy = status.turn_running || status.command_running;

  // Home's chat table and this page's own rail both link to `#/chat/<id>`;
  // this is what makes following one actually switch chats -- the same
  // request `Sidebar.jsx` sends for the old client, since opening a chat is
  // a session action (refused while a turn owns the session), not a
  // client-side route change.
  const [openError, setOpenError] = useState('');
  useEffect(() => {
    if (route.page !== 'chat' || !route.arg || route.arg === status.chat) return undefined;
    let live = true;
    post('/api/open', {slug: status.slug, chat: route.arg})
      .then(() => live && setOpenError(''))
      .catch((error) => live && setOpenError(String(error?.message ?? error)));
    return () => {
      live = false;
    };
  }, [route.page, route.arg, status.slug, status.chat]);

  // The Lean names a `<Lean/>` block can dot and click: real ledger item
  // names from `/api/graph`, the one endpoint that carries them. A name not
  // recorded there is not clickable, which is the honest state -- no
  // declaration-lookup endpoint exists yet for anything else.
  const known = useMemo(() => (graph.data ? graph.data.nodes.map((node) => node.name) : []), [graph.data]);
  const nameInfo = useMemo(() => {
    const map = new Map();
    for (const node of graph.data?.nodes ?? []) map.set(node.name, node);
    return map;
  }, [graph.data]);

  const onLeanName = (token, event) => {
    const node = nameInfo.get(token);
    onPeek?.({
      name: token,
      kind: node ? node.kind : <Absent kind="unreported" />,
      source: node ? node.artifacts[0] || <Absent kind="unreported" /> : <Absent kind="unreported" />,
      informal: node ? node.statement || <Absent kind="unreported" /> : <Absent kind="unreported" />,
      x: event.clientX,
      y: event.clientY,
    });
  };

  const errors = [summary, chats, tree].map((panel) => panel.error).filter(Boolean);
  if (errors.length) return <p className="panel__error">{errors[0]}</p>;
  if (!summary.data || !chats.data || !tree.data) return <p className="panel__note">Reading the chat…</p>;

  const goal = (summary.data.goal || '').trim();
  if (!goal) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Chat</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="No chats yet"
          line="The first message creates the chat named main. Slash commands work before any turn has run: /goal, /env, /help."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const {running, entries, lines} = headerStats({messages, status, runningTool, treeData: tree.data});
  const forkCount = tree.data.entries.filter(isFork).length;
  const abandonCount = tree.data.entries.filter(isAbandon).length;
  const branchesTotal = forkCount + abandonCount;

  const currentChat = chats.data.find((chat) => chat.id === status.chat);
  const attention = jobs.data?.attention ?? [];
  const backend = models.data?.backend;

  const main = (
    <div className="wb-chat-main">
      <Header chatLabel={status.chat || 'main'} running={running} entries={entries} lines={lines} active={route.page} go={go} />
      <Transcript
        messages={messages}
        prompts={prompts}
        loaded={loaded}
        known={known}
        onAnswer={answer}
        onLeanName={onLeanName}
      />
      <Composer
        draft={draft}
        commands={commands}
        busy={busy}
        queued={status.queued ?? 0}
        refusal={refusal}
        runningTool={runningTool}
        onDraft={setDraft}
        onSend={send}
        onBlocked={blocked}
        onCancel={cancel}
      />
    </div>
  );

  if (!fullWidth) {
    // Docked beside another page: a 200px rail would leave 180px of a 380px
    // aside for the transcript, so it is left off rather than squeezed in.
    return main;
  }

  return (
    <div className="wb-chat-full">
      <div className="wb-chat-rail">
        <div className="wb-chat-rail__section">
          <Label>Chats</Label>
          <div className="wb-chat-rail__chats">
            {chats.data.map((chat) => (
              <a
                key={chat.id}
                href={`#/chat/${chat.id}`}
                className={chat.id === status.chat ? 'wb-chat-rail__chat wb-chat-rail__chat--live' : 'wb-chat-rail__chat'}
                onClick={(event) => {
                  event.preventDefault();
                  go({page: 'chat', arg: chat.id});
                }}
              >
                {chat.title || chat.id}
              </a>
            ))}
          </div>
          {openError ? <div className="wb-chat-rail__error">{openError}</div> : null}
        </div>

        <div className="wb-chat-rail__section">
          <Label>This chat</Label>
          <Facts
            mono
            rows={[
              ['model', orAbsent(status.model)],
              ['backend', orAbsent(backend)],
              ['thread', <Absent key="thread" kind="unreported" />],
              ['turns', orAbsent(currentChat?.turns)],
              ['branches', abandonCount ? `${branchesTotal} · ${abandonCount} abandoned` : branchesTotal],
              ['tokens', <Absent key="tokens" kind="unreported" />],
              ['cost', <Absent key="cost" kind="unreported" />],
            ]}
          />
        </div>

        <div className="wb-chat-rail__section">
          <Label>Waiting on</Label>
          {attention.length ? (
            <div className="wb-chat-rail__waiting">
              {attention.map((item) => (
                <div key={item.id}>
                  {item.id} to {item.summary}
                </div>
              ))}
            </div>
          ) : (
            <div className="panel__note">nothing waiting</div>
          )}
        </div>

        <a
          href="#/tree"
          className="wb-chat-rail__link"
          onClick={(event) => {
            event.preventDefault();
            go('tree');
          }}
        >
          Conversation tree →
        </a>
      </div>
      {main}
    </div>
  );
}
