// Ledger, graph half: `/api/graph` drawn as family-coloured, staleness-aware
// SVG. The list half is a later shipment (task-13-brief.md, Step 6), so the
// `list · graph` toggle shows `List` disabled rather than switching to a
// table that would draw zero rows and read as "the ledger has nothing in
// it" -- which is a different claim from "this view is not built yet".
//
// `family` (per node) and `style` (per edge) are read straight off the
// payload and turned into a CSS variable lookup, never recomputed from
// `kind`: `panels/vocabulary.py` is the one place that classification is
// allowed to happen, and `family`'s five values (`result`, `research`,
// `concept`, `document`, `other`) are exactly the five design tokens
// (`--result`, `--research`, `--concept`, `--document`, `--other`) by
// construction, so `var(--${family})` is the whole mapping -- not a table
// this file would have to keep in sync with `vocabulary.py`'s. A `style`
// this page does not recognise (only `solid` and `dashed` exist today) falls
// back to solid, the same "print the word, never guess a family" shape
// `Pill.jsx`'s own fallback uses.
//
// A stale edge is drawn in `--error`, per node and edge staying exactly what
// `panels/record.py:graph` computed: "the relation was recorded against a
// version of the item that has since been revised... not known to be false,
// and not known to still hold." That sentence, not a fabricated "recorded
// against v3" -- the edge payload carries no version numbers to be specific
// with, only `stale: true`.
//
// Layout is dagre (already a dependency; `panels/Graph.jsx`, the old
// three-column client's graph, uses it too -- though that component infers
// family and edge style from `kind` client-side, which is exactly the thing
// this task's brief rules out, so it is not reused here beyond the idea of
// using dagre at all), scaled to fit inside the fixed 760x420 frame the
// design specifies. There is no pan or zoom: the old panel had both, but
// nothing in the brief or the prototype's own copy for this page asks for
// them, and a fixed fit keeps every node in view without an interaction
// budget this task was not given.

import {useMemo, useState} from 'react';
import dagre from 'dagre';
import Absent, {orAbsent} from '../components/Absent.jsx';
import Empty from '../components/Empty.jsx';
import Facts from '../components/Facts.jsx';
import Label from '../components/Label.jsx';
import useHash from '../session/useHash.js';
import usePanel from '../session/usePanel.js';
import useSession from '../session/useSession.js';

const VIEW_W = 760;
const VIEW_H = 420;
const PAD = 20;
const NODE_H = 34;
const CHAR = 7;
const MIN_W = 80;
const MAX_W = 180;
//: What the "F" badge claims, quoted from the prototype's own legend
//: (Hardy Workbench.dc.html:360) rather than paraphrased.
const F_MEANING = 'formal evidence recorded — a claim in the ledger; the verdict is on Results';
const LIST_NOTE = 'The list view is a later shipment; only the graph is built here.';

function boxWidth(label) {
  return Math.max(MIN_W, Math.min(MAX_W, 20 + label.length * CHAR));
}

function fitted(label, width) {
  const room = Math.max(1, Math.floor((width - 14) / CHAR));
  return label.length <= room ? label : `${label.slice(0, Math.max(0, room - 1))}…`;
}

/** Place every node and route every edge with dagre, then report the
 *  bounding box so the caller can scale the whole drawing to fit the frame. */
function layout(nodes, edges) {
  const graph = new dagre.graphlib.Graph({multigraph: true});
  graph.setGraph({rankdir: 'LR', nodesep: 14, ranksep: 44, marginx: 10, marginy: 10});
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) {
    graph.setNode(node.id, {width: boxWidth(node.name || node.id), height: NODE_H});
  }
  const known = new Set(nodes.map((node) => node.id));
  const drawn = edges.filter((edge) => known.has(edge.source) && known.has(edge.target));
  for (const edge of drawn) graph.setEdge(edge.source, edge.target, {}, edge.id);
  dagre.layout(graph);
  const placed = nodes.map((node) => ({...node, box: graph.node(node.id)}));
  const routed = drawn.map((edge) => ({
    ...edge,
    points: graph.edge({v: edge.source, w: edge.target, name: edge.id}).points,
  }));
  const size = graph.graph();
  return {placed, routed, width: size.width || 1, height: size.height || 1, dropped: edges.length - drawn.length};
}

const path = (points) => points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ');

export default function Ledger() {
  const {revision, setDraft} = useSession();
  const [, go] = useHash();
  const graphPanel = usePanel('/api/graph', revision);
  const [openId, setOpenId] = useState('');

  const laid = useMemo(
    () => (graphPanel.data ? layout(graphPanel.data.nodes, graphPanel.data.edges) : null),
    [graphPanel.data],
  );

  if (graphPanel.error) return <p className="panel__error">{graphPanel.error}</p>;
  if (!graphPanel.data || !laid) return <p className="panel__note">Reading the ledger...</p>;

  const {nodes, edges, revision: rev, tones} = graphPanel.data;
  const empty = nodes.length === 0;

  if (empty) {
    return (
      <div className="wb-page-body">
        <div className="wb-page-head">
          <span className="page-title">Ledger</span>
          <span className="wb-page-subtitle">fresh project · nothing recorded</span>
        </div>
        <Empty
          title="Ledger is empty"
          line="0 items · 0 relations · revision 0. Items appear when a theorem is saved, an assumption approved, or a research note recorded."
        />
        <Label>what will appear here</Label>
        <div className="panel__note">
          The same layout as a running project, with real counts. Zero is shown as <Absent kind="zero" />; a
          figure the backend has not supplied is shown as <Absent kind="unreported" />.
        </div>
      </div>
    );
  }

  const scale = Math.min(
    1,
    (VIEW_W - PAD * 2) / laid.width,
    (VIEW_H - PAD * 2) / laid.height,
  );
  const offsetX = (VIEW_W - laid.width * scale) / 2;
  const offsetY = (VIEW_H - laid.height * scale) / 2;

  const staleEdges = laid.routed.filter((edge) => edge.stale);
  const open = laid.placed.find((node) => node.id === openId) || null;

  return (
    <div className="wb-page-body">
      <div className="wb-page-head">
        <span className="page-title">Ledger</span>
        <span className="wb-page-subtitle">
          {nodes.length} items · {edges.length} relations · revision {rev} · {staleEdges.length} stale
        </span>
      </div>

      <div className="wb-ledger__toggle">
        <button type="button" className="wb-ledger__toggle-btn" disabled title={LIST_NOTE}>
          List
        </button>
        <button type="button" className="wb-ledger__toggle-btn wb-ledger__toggle-btn--on">
          Graph
        </button>
        <span className="panel__note">{LIST_NOTE}</span>
      </div>

      <div className="wb-ledger__legend">
        <span><span className="wb-ledger__swatch" style={{background: 'var(--result)'}} /> result</span>
        <span><span className="wb-ledger__swatch" style={{background: 'var(--research)'}} /> research</span>
        <span><span className="wb-ledger__swatch" style={{background: 'var(--concept)'}} /> concept</span>
        <span><span className="wb-ledger__swatch" style={{background: 'var(--document)'}} /> document</span>
        <span><span className="wb-ledger__swatch" style={{background: 'var(--other)'}} /> other</span>
        <span className="wb-ledger__legend-sep" />
        <span>— dependency (solid)</span>
        <span>╌╌ intention (dashed)</span>
        <span style={{color: 'var(--error)'}}>— stale</span>
        <span className="wb-ledger__legend-sep" />
        <span title={F_MEANING}>
          <span className="wb-ledger__f-badge">F</span> {F_MEANING}
        </span>
      </div>

      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="wb-ledger__svg" role="img" aria-label="The project ledger as a graph.">
        <defs>
          <marker id="ledger-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto">
            <path d="M0 0 L8 4 L0 8 z" fill="var(--muted)" />
          </marker>
          <marker id="ledger-arrow-stale" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto">
            <path d="M0 0 L8 4 L0 8 z" fill="var(--error)" />
          </marker>
        </defs>
        <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
          {laid.routed.map((edge) => (
            <path
              key={edge.id}
              d={path(edge.points)}
              fill="none"
              stroke={edge.stale ? 'var(--error)' : 'var(--muted)'}
              strokeWidth="1.6"
              strokeDasharray={edge.style === 'dashed' ? '6 4' : undefined}
              markerEnd={`url(#${edge.stale ? 'ledger-arrow-stale' : 'ledger-arrow'})`}
            >
              <title>{`${edge.kind}${edge.stale ? ' (stale)' : ''}`}</title>
            </path>
          ))}
          {laid.placed.map((node) => {
            const {x, y, width, height} = node.box;
            const formal = node.evidence.includes('formal');
            return (
              <g
                key={node.id}
                className="wb-ledger__node"
                transform={`translate(${x - width / 2} ${y - height / 2})`}
                onClick={() => setOpenId((current) => (current === node.id ? '' : node.id))}
              >
                <rect
                  width={width}
                  height={height}
                  rx="6"
                  fill={`var(--${node.family}, var(--other))`}
                  stroke={node.id === openId ? 'var(--accent)' : 'var(--border)'}
                  strokeWidth={node.id === openId ? 2 : 1}
                />
                <text x="8" y={height / 2 + 4} fill="var(--fg)" fontFamily="var(--mono)" fontSize="11">
                  {fitted(node.name || node.id, width)}
                </text>
                {formal ? (
                  <g>
                    <title>{F_MEANING}</title>
                    <circle cx={width - 11} cy="11" r="8" fill="var(--accent)" />
                    <text x={width - 11} y="15" textAnchor="middle" fill="var(--panel)" fontSize="10" fontWeight="700">
                      F
                    </text>
                  </g>
                ) : null}
                <title>{`${node.name || node.id} (${node.kind})`}</title>
              </g>
            );
          })}
        </g>
      </svg>

      {laid.dropped ? (
        <div className="panel__note">{laid.dropped} relation(s) point outside the current heads and are not drawn.</div>
      ) : null}

      {staleEdges.length ? (
        <div className="wb-ledger__stale">
          <span className="section-label">stale relations · {staleEdges.length}</span>
          {staleEdges.map((edge) => (
            <div key={edge.id} className="wb-ledger__stale-line">
              <span style={{fontFamily: 'var(--mono)'}}>
                {edge.source} —{edge.kind}→ {edge.target}
              </span>
              {' — recorded against a version of one of these items that has since been revised; nothing has re-checked it. '}
              Not known to be false, and not known to still hold.
            </div>
          ))}
        </div>
      ) : null}

      {open ? (
        <div className="wb-ledger__drawer">
          <div className="wb-ledger__drawer-head">
            <strong>{open.name || open.id}</strong>
            <button type="button" className="button" onClick={() => setOpenId('')}>
              Close
            </button>
          </div>
          <Facts
            rows={[
              ['id', open.id],
              ['kind', open.kind],
              ['origin', open.origin],
              ['evidence', open.evidence.length ? open.evidence.join(', ') : <Absent kind="na" />],
              ['artifacts', open.artifacts.length ? open.artifacts.join(', ') : <Absent kind="na" />],
              ['research', open.research ?? <Absent kind="na" />],
              [
                'obligations',
                Object.keys(open.obligations).length ? (
                  <span key="obl" style={{display: 'flex', gap: 8, flexWrap: 'wrap'}}>
                    {Object.entries(open.obligations).map(([status, count]) => (
                      <span key={status} style={{color: `var(--${tones[status]})`}}>
                        {count} {status}
                      </span>
                    ))}
                  </span>
                ) : (
                  <Absent kind="zero" />
                ),
              ],
            ]}
          />
          {open.statement ? (
            <pre className="wb-ledger__statement">{open.statement}</pre>
          ) : (
            <div className="panel__note">No statement recorded.</div>
          )}
          <div style={{display: 'flex', gap: 8}}>
            <button
              type="button"
              className="button"
              onClick={() => {
                // Populates the composer's draft rather than submitting: the
                // same rule Tree.jsx (Task 13a) applies to /fork -- /delegate
                // takes options a click cannot guess (--checks, --mode), and
                // the click here is not on the Chat route to begin with.
                setDraft(`/delegate ${open.id} `);
                go('chat');
              }}
            >
              Put /delegate {open.id} in composer
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
