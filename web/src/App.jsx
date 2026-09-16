// The existing three-column layout, now reading the session through context
// instead of holding it. `SessionProvider` owns the reducer, the fetches and
// the event stream; this component only draws. It is reduced to this shape
// for Task 8 and deleted once the new shell (`pages/`, `components/`) takes
// over drawing in a later task.

import {useMemo} from 'react';
import Chat from './Chat.jsx';
import Composer from './Composer.jsx';
import ModelPicker from './ModelPicker.jsx';
import Panels from './Panels.jsx';
import SessionProvider from './session/SessionProvider.jsx';
import useSession from './session/useSession.js';
import Sidebar from './Sidebar.jsx';

function Layout() {
  const {
    status, commands, projects, messages, prompts, draft, refusal, runningTool, revision, loaded,
    send, answer, cancel, setDraft, blocked, refreshProjects, setProjects,
  } = useSession();

  const busy = status.turn_running || status.command_running;
  const title = useMemo(() => [status.slug, status.chat].filter(Boolean).join(' / '), [status.slug, status.chat]);

  return (
    <div className="layout">
      <Sidebar
        projects={projects}
        slug={status.slug}
        chat={status.chat}
        onProjects={setProjects}
        onRefresh={refreshProjects}
      />

      <main className="main">
        <header className="header">
          <span className="header__title">{title || 'Hardy'}</span>
          <ModelPicker model={status.model} busy={busy} revision={revision} onSend={send} />
          {busy ? <span className="header__busy">working</span> : null}
        </header>
        <Chat
          messages={messages}
          prompts={prompts}
          loaded={loaded}
          onAnswer={answer}
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
      </main>

      {/* `send` and not a private poster: an `/import` or a `/fork` submitted
          from a panel is the same line the user could have typed, and it is
          echoed, refused and reported through the one path every other line
          takes. `setDraft` is the exception the graph needs -- "Delegate"
          writes the line and leaves sending it to the user. */}
      <Panels revision={revision} onSend={send} onDraft={setDraft} />
    </div>
  );
}

export default function App() {
  return (
    <SessionProvider>
      <Layout />
    </SessionProvider>
  );
}
