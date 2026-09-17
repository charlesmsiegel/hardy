// Publications: `/api/publications` (`panels/record.py: publications`), which
// answers with exactly two halves -- `items` (every current `ProjectItem`'s
// publication presentation: `visibility`/`role`/`family`/`link_source_kinds`)
// and `candidates` (one row per `(scope, root)` pair a `Scope.must_prove`
// already commits to, previewing what `/project publish` would report
// without running it) -- and this page's own layout follows that same split:
// left is the publish builder over `candidates`, right is the item table plus
// the link/mark builders over `items`.
//
// **There is no bundle list here, and the prototype's own two-pane
// list-of-bundles/bundle-viewer (`Hardy Workbench.dc.html:607-661`) cannot be
// built from this endpoint.** `publications()`'s own docstring is explicit:
// the actual compile step (`PublishWorkflow.publish`, `workflows/publish.py:
// 167-189`) "is genuinely mutating (it creates `publications/<name>/` on
// disk) and is not reachable from this GET." So there is no `sylow-lt-60
// compiled · 7 pages`, no `writeup.tex`/`compile.log`/`.pdf` tab strip, and no
// per-bundle presentation-decisions table anywhere in this shipment's API --
// drawing them would mean inventing a bundle. What *is* real and shown
// instead: every current item's publication state, and every root a scope
// already commits to proving, with a live preview (`ready`/`closure`/`gaps`)
// of what publishing it would report.
//
// **The design's commands and its refusal sentence are both fabricated, per
// Task 6's review.** `/publish`, `/link` and `/mark` are not registered --
// only `/project` is (`handlers.py:1516-1519`) -- and the real forms are
// `/project publish ITEM --scope SCOPE --output BUNDLE`, `/project link
// SOURCE illustrates|documents TARGET`, `/project mark ITEM
// internal|public|omitted` (`prompts/terminal.py:3-7`). The design's own
// invented refusal ('/publish refused: bundle "sylow-lt-60" already exists.
// Pass a new --name.') gets both the flag wrong (`--output`, never `--name`)
// and the sentence itself: `tui/project.py:79-80` catches `(ValueError,
// OSError)` around every verb and prints `f"Could not update publication:
// {error}"` -- a bare `FileExistsError` from `output.mkdir()`'s exclusive
// creation (`workflows/publish.py:179`), not a polished sentence with the
// bundle name and a suggestion baked in. This page never writes that
// sentence itself: Publish/Link/Mark all run the real command through
// `send()` (the same `/api/input` channel `pages/Jobs.jsx`'s confirm cards
// use), and whatever `useSession().refusal` reports -- the dispatcher's own
// words, verbatim -- is what prints.
//
// Every command here is genuinely session-changing (a bundle, once created,
// is immutable and cannot be replaced under the same name; a mark or a link
// is a ledger write), so each is offered through the composer's own
// `ConfirmCard`, following `pages/Jobs.jsx`'s pattern rather than `Results.jsx`'s
// `setDraft`-into-the-composer one: unlike `/delegate`, none of these three
// takes an option a dropdown here cannot already supply, so there is nothing
// left for a human to add by hand before sending.
//
// **Family filtering (this task's brief, and `publications()`'s own
// docstring): "research-family kinds can carry publication fields but can
// never appear in a publication unless they are an explicit root."** A
// `goal`/`approach`/`question`/`conjecture`/`research_note` has a
// `publication_visibility` and can be marked `public` the same as a theorem,
// but nothing makes it a `Scope.must_prove` root in practice -- so the items
// table below can be filtered by family (chips, client-side, the same
// "already-fetched list" reasoning `Ledger.jsx`'s own filters give), and a
// note beside the filter says why a `research`-family row showing `public`
// here is not the same claim as it being publishable.
//
// `role` (`PublicationRole`: main/supporting/background/illustration/
// exposition/appendix) is read-only in this shipment -- no command sets it,
// only `visibility` does (`ProjectOperations.mark`, `workflows/interactive/
// project.py:54-68`, two arguments) -- so a `null` role prints `Absent
// kind="na"`, the same "an optional field most items simply don't have"
// reading `Ledger.jsx` gives its own `research` field, not `unreported`: the
// value is not missing from a source that assigns one, this shipment's own
// commands never assign one at all.

import {useState} from 'react';
import Absent from '../components/Absent.jsx';
import {ConfirmCard} from '../components/Cards.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import Pill from '../components/Pill.jsx';
import Table from '../components/Table.jsx';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const VISIBILITIES = ['public', 'internal', 'omitted'];

function visibilityPill(item) {
  return <Pill tone={item.visibility_tone}>{item.visibility}</Pill>;
}

function familyBadge(family, key) {
  return (
    <span key={key} className="wb-publications__family-badge" style={{background: `var(--${family}, var(--other))`}}>
      {family}
    </span>
  );
}

function candidateKey(candidate) {
  return `${candidate.item}::${candidate.scope}`;
}

/** The distinct families actually present, "all" first -- the same
 *  read-what's-there-not-the-full-enum reasoning `Ledger.jsx`'s `distinct`
 *  gives its own filter options, so a fresh project with only `result` items
 *  offers one chip, not five. */
function familiesPresent(items) {
  return [...new Set(items.map((item) => item.family))].sort();
}

export default function Publications({arg}) {
  const {revision, send, refusal} = useSession();
  const panel = usePanel('/api/publications', revision);
  const [familyFilter, setFamilyFilter] = useState('all');
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [outputName, setOutputName] = useState('');
  const [selectedItem, setSelectedItem] = useState(arg || null);
  const [markVisibility, setMarkVisibility] = useState('public');
  const [linkSourceId, setLinkSourceId] = useState('');
  const [linkKind, setLinkKind] = useState('');
  const [linkTargetId, setLinkTargetId] = useState('');
  //: The one confirm card open at a time, wherever it was raised from --
  //: Publish (left), Mark or Link (right) -- the same single-slot shape
  //: `pages/Jobs.jsx` uses for its own five commands.
  const [confirm, setConfirm] = useState(null);

  if (panel.error) return <p className="panel__error">{panel.error}</p>;
  if (!panel.data) return <p className="panel__note">Reading publications...</p>;

  const {items, candidates, revision: dataRevision} = panel.data;

  if (items.length === 0) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Publications</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="No bundles"
          line="/project publish needs at least one saved theorem. Nothing to build from."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const runCommand = (command) => {
    send(command);
    setConfirm(null);
  };

  const families = familiesPresent(items);
  const filteredItems = familyFilter === 'all' ? items : items.filter((item) => item.family === familyFilter);
  const itemById = new Map(items.map((item) => [item.id, item]));
  const selected = selectedItem ? itemById.get(selectedItem) || null : null;

  const chosenCandidate = candidates.find((candidate) => candidateKey(candidate) === selectedCandidate) || null;
  const publishCommand =
    chosenCandidate && outputName.trim()
      ? `/project publish ${chosenCandidate.item} --scope ${chosenCandidate.scope} --output ${outputName.trim()}`
      : null;

  const linkSource = linkSourceId ? itemById.get(linkSourceId) || null : null;
  const linkTarget = linkTargetId ? itemById.get(linkTargetId) || null : null;
  const linkableSources = items.filter((item) => item.link_source_kinds.length > 0);
  const linkCommand =
    linkSource && linkKind && linkTarget && linkSource.id !== linkTarget.id
      ? `/project link ${linkSource.id} ${linkKind} ${linkTarget.id}`
      : null;

  const markCommand = selected ? `/project mark ${selected.id} ${markVisibility}` : null;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Publications</span>
        <span className="wb-page-subtitle">
          {items.length} item{items.length === 1 ? '' : 's'} · {candidates.length} publishable root
          {candidates.length === 1 ? '' : 's'} · revision {dataRevision}
        </span>
      </div>

      {confirm ? (
        <ConfirmCard
          command={confirm.command}
          title={confirm.title}
          facts={confirm.facts}
          onYes={() => runCommand(confirm.command)}
          onNo={() => setConfirm(null)}
        />
      ) : null}
      {refusal ? <div className="wb-publications__refusal">{refusal}</div> : null}

      <div className="wb-publications">
        <div className="wb-publications__left">
          <div className="wb-section">
            <Label>{`Publishable roots · ${candidates.length}`}</Label>
            {candidates.length ? (
              <Table
                head={['item', 'scope', 'ready', 'closure', 'gaps']}
                onPick={setSelectedCandidate}
                selected={selectedCandidate}
                rows={candidates.map((candidate) => ({
                  key: candidateKey(candidate),
                  cells: [
                    <div key="item">
                      {familyBadge(candidate.family, 'family')}{' '}
                      <span style={{fontFamily: 'var(--mono)'}}>{candidate.name}</span>{' '}
                      <span className="panel__note" style={{margin: 0}}>
                        {candidate.item}
                      </span>
                    </div>,
                    <span key="scope" style={{fontFamily: 'var(--mono)', color: 'var(--muted)'}}>
                      {candidate.scope}
                    </span>,
                    <Pill key="ready" tone={candidate.ready ? 'accent' : 'warning'}>
                      {candidate.ready ? 'ready' : 'not ready'}
                    </Pill>,
                    <span key="closure">{candidate.closure}</span>,
                    <span key="gaps" style={{fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)'}}>
                      {candidate.gaps.length ? candidate.gaps.join(', ') : <Absent kind="zero" />}
                    </span>,
                  ],
                }))}
              />
            ) : (
              <div className="panel__note">
                No scope's <code>must_prove</code> names a root yet -- nothing here for /project publish to build
                from.
              </div>
            )}
          </div>

          <div className="wb-section">
            <Label>Publish · builds the real command; nothing runs until you confirm</Label>
            <div className="wb-card">
              <Facts
                rows={[
                  ['item', chosenCandidate ? chosenCandidate.item : <Absent kind="na" />],
                  ['scope', chosenCandidate ? chosenCandidate.scope : <Absent kind="na" />],
                  [
                    'output name',
                    <input
                      key="output"
                      type="text"
                      className="wb-publications__input"
                      value={outputName}
                      placeholder="bundle-name"
                      onChange={(event) => setOutputName(event.target.value)}
                    />,
                  ],
                ]}
              />
              <div className="panel__note">
                {chosenCandidate
                  ? 'Pick a row above to publish, then name the output bundle. Bundles are immutable -- an ' +
                    'existing --output name is refused, and the refusal above is the real one, not a guess.'
                  : 'Pick a publishable root above first.'}
              </div>
              <div className="wb-publications__composed">
                <span className="wb-publications__command">{publishCommand || '/project publish ...'}</span>
                <button
                  type="button"
                  className="button"
                  disabled={!publishCommand}
                  onClick={() =>
                    setConfirm({
                      command: publishCommand,
                      title: `Publish ${chosenCandidate.item} as "${outputName.trim()}"?`,
                      facts: [
                        ['scope', chosenCandidate.scope],
                        ['ready', chosenCandidate.ready ? 'yes' : 'no'],
                      ],
                    })
                  }
                >
                  Publish…
                </button>
              </div>
            </div>
          </div>
        </div>

        <div className="wb-publications__right">
          <div className="wb-section">
            <Label>{`Items · ${filteredItems.length} of ${items.length}`}</Label>
            <div className="wb-publications__family-filter">
              <button
                type="button"
                className={
                  familyFilter === 'all'
                    ? 'wb-publications__chip wb-publications__chip--on'
                    : 'wb-publications__chip'
                }
                onClick={() => setFamilyFilter('all')}
              >
                all
              </button>
              {families.map((family) => (
                <button
                  key={family}
                  type="button"
                  className={
                    familyFilter === family
                      ? 'wb-publications__chip wb-publications__chip--on'
                      : 'wb-publications__chip'
                  }
                  onClick={() => setFamilyFilter(family)}
                >
                  {family}
                </button>
              ))}
            </div>
            <div className="panel__note">
              Filters this list only. Visibility and role apply to every kind here, including research kinds like
              goal or conjecture -- but only an item a scope's <code>must_prove</code> names as a root (left,
              "publishable roots") can actually enter a publication. A research-family row marked{' '}
              <Pill tone="accent">public</Pill> here is a presentation decision, not a claim that it can be
              published.
            </div>
            <Table
              head={['id', 'kind', 'name', 'visibility', 'role', 'link source of']}
              onPick={setSelectedItem}
              selected={selectedItem || ''}
              rows={filteredItems.map((item) => ({
                key: item.id,
                cells: [
                  <span key="id" style={{fontFamily: 'var(--mono)', color: 'var(--muted)'}}>
                    {item.id}
                  </span>,
                  familyBadge(item.family, 'family'),
                  <span key="name" style={{fontFamily: 'var(--mono)'}}>
                    {item.name}
                  </span>,
                  visibilityPill(item),
                  <span key="role" style={{color: 'var(--muted)'}}>{item.role ?? <Absent kind="na" />}</span>,
                  <span key="link" style={{fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)'}}>
                    {item.link_source_kinds.length ? item.link_source_kinds.join(', ') : <Absent kind="na" />}
                  </span>,
                ],
              }))}
            />
          </div>

          <div className="wb-publications__builders">
            <div className="wb-section">
              <Label>Mark</Label>
              <div className="wb-card">
                <Facts rows={[['item', selected ? `${selected.id} ${selected.name}` : <Absent kind="na" />]]} />
                <div className="wb-publications__pill-row">
                  {VISIBILITIES.map((visibility) => (
                    <button
                      key={visibility}
                      type="button"
                      className={
                        markVisibility === visibility
                          ? 'wb-publications__chip wb-publications__chip--on'
                          : 'wb-publications__chip'
                      }
                      onClick={() => setMarkVisibility(visibility)}
                    >
                      {visibility}
                    </button>
                  ))}
                </div>
                <div className="wb-publications__composed">
                  <span className="wb-publications__command">{markCommand || '/project mark ...'}</span>
                  <button
                    type="button"
                    className="button"
                    disabled={!markCommand}
                    onClick={() =>
                      setConfirm({
                        command: markCommand,
                        title: `Mark ${selected.id} ${markVisibility}?`,
                        facts: [['currently', selected.visibility]],
                      })
                    }
                  >
                    Mark…
                  </button>
                </div>
              </div>
            </div>

            <div className="wb-section">
              <Label>Link</Label>
              <div className="wb-card">
                {linkableSources.length ? (
                  <>
                    <div className="wb-publications__field">
                      <span className="panel__note">source (illustrates/documents only)</span>
                      <select
                        value={linkSourceId}
                        onChange={(event) => {
                          setLinkSourceId(event.target.value);
                          setLinkKind('');
                        }}
                      >
                        <option value="">pick a source...</option>
                        {linkableSources.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.id} {item.name}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="wb-publications__pill-row">
                      {(linkSource?.link_source_kinds || []).map((kind) => (
                        <button
                          key={kind}
                          type="button"
                          className={
                            linkKind === kind ? 'wb-publications__chip wb-publications__chip--on' : 'wb-publications__chip'
                          }
                          onClick={() => setLinkKind(kind)}
                        >
                          {kind}
                        </button>
                      ))}
                    </div>
                    <div className="wb-publications__field">
                      <span className="panel__note">target (any other item)</span>
                      <select value={linkTargetId} onChange={(event) => setLinkTargetId(event.target.value)}>
                        <option value="">pick a target...</option>
                        {items
                          .filter((item) => item.id !== linkSourceId)
                          .map((item) => (
                            <option key={item.id} value={item.id}>
                              {item.id} {item.name}
                            </option>
                          ))}
                      </select>
                    </div>
                    <div className="wb-publications__composed">
                      <span className="wb-publications__command">{linkCommand || '/project link ...'}</span>
                      <button
                        type="button"
                        className="button"
                        disabled={!linkCommand}
                        onClick={() =>
                          setConfirm({
                            command: linkCommand,
                            title: `Link ${linkSource.id} ${linkKind} ${linkTarget.id}?`,
                          })
                        }
                      >
                        Link…
                      </button>
                    </div>
                  </>
                ) : (
                  <div className="panel__note">
                    <Absent kind="na" /> -- no current item is an <code>example</code>, <code>exposition</code> or{' '}
                    <code>document_fragment</code>: only those kinds may originate an{' '}
                    <code>illustrates</code>/<code>documents</code> link (<code>vocabulary.link_source_kinds</code>).
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
