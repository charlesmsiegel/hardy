// The project ledger as a picture: every current item, every relation between
// them, and which of those relations nothing has re-checked.
//
// Three things the drawing is careful about, because each is a claim.
//
// Colour is by kind *family* and not by kind, so a reader learns four groups
// rather than twenty words: what has been established, what is being asked,
// what a thing is, and what has been written up. The "F" badge says one
// further thing, and says exactly that much: a formal `EvidenceRef` is
// RECORDED against this node. An evidence reference is claimed capability
// provenance -- whoever produced it said what it was -- and this panel never
// re-checks it, so the badge is not a kernel verdict and must not be read as
// one. What the kernel found is in the record, under the audit that found it.
//
// Edge style is by family too: a hard dependency is drawn solid, an intention
// dashed, a sameness dotted, and everything else thin. A `stale` edge is drawn
// in the error colour, because it is a relation recorded against a version of
// an item that has since been revised: it is not known to be false, and it is
// not known to still hold, and the difference between those is the whole point
// of drawing it differently.
//
// Delegating does not delegate. "Delegate" puts `/delegate <id> ` in the
// composer and stops: the command takes an objective and options the panel
// cannot guess, and starting a background worker on a half-specified objective
// is not something a single click should be able to do.

import dagre from 'dagre';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import usePanel from '../session/usePanel.js';

//: Which of the four colours a node's kind belongs to. A kind nobody listed
//: here falls through to `other`, which is drawn but not claimed to be one of
//: the four.
const FAMILY = {
  theorem: 'result', lemma: 'result', proposition: 'result', corollary: 'result',
  claim: 'result', definition: 'result',
  question: 'research', conjecture: 'research', goal: 'research', approach: 'research',
  research_note: 'research',
  concept: 'concept', representation: 'concept', declaration: 'concept',
  standard_object: 'concept', external_result: 'concept',
  exposition: 'document', section: 'document', chapter: 'document', book: 'document',
  document_fragment: 'document', example: 'document', computation: 'document',
};

const STROKE = {
  depends_on: 'solid', uses: 'solid', typed_by: 'solid', blocked_by: 'solid',
  poses: 'dashed', targets: 'dashed', pursues: 'dashed', produces: 'dashed',
  counterexample_to: 'dashed', supports: 'dashed',
  equivalent_to: 'dotted', identified_with: 'dotted', transported_from: 'dotted',
};

const NODE_HEIGHT = 34;
//: Roughly one character of the 12px monospace face, for sizing a box to its
//: label before anything has been measured.
const CHAR = 7.2;
const MIN_WIDTH = 96;
const MAX_WIDTH = 260;
const ZOOM = 1.12;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 3;
//: Past this much pointer movement the gesture was a pan, and the click that
//: ends it must not also open a node.
const DRAG_SLOP = 4;
//: Slack left around the graph when it is fitted to the frame. Dagre's own
//: reported size came out a couple of pixels short of the real extent of the
//: boxes it had placed, so a fit computed straight from it clipped the last
//: node by a hair; this is wider than that discrepancy and reads as a margin.
const FIT_PAD = 12;

//: What the "F" badge claims, in the badge's own tooltip and in the bar, in
//: the same words: a reader who hovers and a reader who does not must not come
//: away with different beliefs about what it means.
const BADGE_MEANING = 'formal evidence recorded in the ledger; the audit verdict is in the record, not this badge';

const labelled = (node) => node.name || node.id;

function boxWidth(node) {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, 26 + labelled(node).length * CHAR));
}

/** Truncate a label to what its box can hold, with an ellipsis when it cannot. */
function fitted(node) {
  const text = labelled(node);
  const room = Math.floor((MAX_WIDTH - 26) / CHAR);
  return text.length <= room ? text : `${text.slice(0, room - 1)}…`;
}

/** Place every node and route every edge with dagre, left to right. */
function laid(nodes, edges) {
  const graph = new dagre.graphlib.Graph({multigraph: true});
  graph.setGraph({rankdir: 'LR', nodesep: 18, ranksep: 56, marginx: 24, marginy: 24});
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) graph.setNode(node.id, {width: boxWidth(node), height: NODE_HEIGHT});
  const known = new Set(nodes.map((node) => node.id));
  // An edge whose endpoint is not among the current heads would otherwise make
  // dagre invent a node -- laid out, taking space, and never drawn, because
  // this panel only draws what `nodes` described.
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

function Drawer({node, onClose, onDraft}) {
  const [copied, setCopied] = useState('');
  const copy = () => {
    const writer = navigator.clipboard;
    if (!writer) {
      setCopied('This browser will not copy for the page; select the id above.');
      return;
    }
    writer
      .writeText(node.id)
      .then(() => setCopied('copied'))
      .catch(() => setCopied('Could not copy; select the id above.'));
  };

  return (
    <div className="drawer">
      <div className="drawer__head">
        <strong>{labelled(node)}</strong>
        <button type="button" className="button" onClick={onClose}>
          Close
        </button>
      </div>
      <dl className="kv">
        <div className="kv__row">
          <dt>id</dt>
          <dd><code>{node.id}</code></dd>
        </div>
        <div className="kv__row">
          <dt>kind</dt>
          <dd>{node.kind}</dd>
        </div>
        <div className="kv__row">
          <dt>origin</dt>
          <dd>{node.origin}</dd>
        </div>
        <div className="kv__row">
          <dt>evidence recorded</dt>
          <dd>{node.evidence.length ? node.evidence.join(', ') : 'none'}</dd>
        </div>
        <div className="kv__row">
          <dt>artifacts</dt>
          <dd>{node.artifacts.length ? node.artifacts.join(', ') : 'none'}</dd>
        </div>
        <div className="kv__row">
          <dt>research</dt>
          <dd>{node.research ?? 'not a research item'}</dd>
        </div>
        <div className="kv__row">
          <dt>obligations</dt>
          <dd>
            {node.obligations.open} open · {node.obligations.resolved} resolved · {node.obligations.other} other
          </dd>
        </div>
      </dl>
      {node.statement ? <pre className="drawer__statement">{node.statement}</pre> : <p className="panel__note">No statement recorded.</p>}
      <div className="card__actions">
        <button type="button" className="button" onClick={copy}>
          Copy id
        </button>
        <button type="button" className="button" onClick={() => onDraft(`/delegate ${node.id} `)}>
          Delegate
        </button>
      </div>
      {copied ? <p className="panel__note">{copied}</p> : null}
    </div>
  );
}

export default function Graph({revision, onDraft}) {
  const {data, error} = usePanel('/api/graph', revision);
  const [view, setView] = useState({x: 0, y: 0, k: 1});
  const [openId, setOpenId] = useState('');
  const frame = useRef(null);
  //: The gesture in flight: where the pointer was last seen, how far it has
  //: travelled, and which node it went down on.
  const drag = useRef(null);

  const graph = useMemo(() => (data ? laid(data.nodes, data.edges) : null), [data]);
  const open = graph ? graph.placed.find((node) => node.id === openId) : null;
  //: Whether this panel has framed its graph yet. The fit happens once per
  //: opening of the tab and never on a refetch: `changed` fires at the end of
  //: every turn, and a view that snapped back to the fit each time would undo
  //: the reader's pan while they were reading.
  const framed = useRef(false);

  /** The whole graph centred in the frame, never magnified past life size. */
  const fit = useCallback(() => {
    const element = frame.current;
    if (!element || !graph) return;
    const box = element.getBoundingClientRect();
    const room = {width: box.width - FIT_PAD, height: box.height - FIT_PAD};
    const k = Math.max(MIN_ZOOM, Math.min(1, room.width / graph.width, room.height / graph.height));
    setView({k, x: (box.width - graph.width * k) / 2, y: (box.height - graph.height * k) / 2});
  }, [graph]);

  useEffect(() => {
    // The rail is 420px wide and a project's graph is wider than that almost
    // at once, so opening the tab at life size shows a corner of the map and
    // leaves the reader to find the rest by dragging.
    if (!graph || framed.current) return;
    framed.current = true;
    fit();
  }, [graph, fit]);

  useEffect(() => {
    const element = frame.current;
    if (!element) return undefined;
    // Registered by hand, not through `onWheel`: React's wheel listener is
    // passive, so `preventDefault` there is ignored and the page scrolls
    // underneath the zoom.
    const wheeled = (event) => {
      event.preventDefault();
      const box = element.getBoundingClientRect();
      const px = event.clientX - box.left;
      const py = event.clientY - box.top;
      setView((current) => {
        const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current.k * (event.deltaY < 0 ? ZOOM : 1 / ZOOM)));
        const ratio = k / current.k;
        // Hold the point under the cursor still while the scale changes.
        return {k, x: px - (px - current.x) * ratio, y: py - (py - current.y) * ratio};
      });
    };
    element.addEventListener('wheel', wheeled, {passive: false});
    return () => element.removeEventListener('wheel', wheeled);
  }, [graph]);

  const down = useCallback((event) => {
    if (event.button !== 0) return;
    // Which node the press landed on is read from the DOM rather than from a
    // handler on the node itself, because capturing the pointer on the svg
    // retargets the `click` that follows to the svg as well: an `onClick` on
    // the node's `<g>` is never called once the drag has captured, so a node
    // could be panned from and never opened. Down and up on the svg are the
    // whole gesture, and `dataset.id` is what says where it began.
    drag.current = {
      x: event.clientX,
      y: event.clientY,
      travelled: 0,
      id: event.target.closest?.('g.node')?.dataset.id ?? '',
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const move = useCallback((event) => {
    const held = drag.current;
    if (!held) return;
    const dx = event.clientX - held.x;
    const dy = event.clientY - held.y;
    drag.current = {
      ...held,
      x: event.clientX,
      y: event.clientY,
      travelled: held.travelled + Math.abs(dx) + Math.abs(dy),
    };
    setView((current) => ({...current, x: current.x + dx, y: current.y + dy}));
  }, []);

  const up = useCallback((event) => {
    if (drag.current && event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const held = drag.current;
    drag.current = null;
    // A press that went nowhere is a click on the node it went down on; one
    // that travelled was a pan, and a pan released over a node must not open
    // it.
    if (!held || !held.id || held.travelled > DRAG_SLOP) return;
    setOpenId((current) => (current === held.id ? '' : held.id));
  }, []);

  if (error) return <p className="panel__error">{error}</p>;
  if (!graph) return <p className="panel__note">Reading the ledger...</p>;
  if (!graph.placed.length) return <p className="panel__note">No ledger items yet.</p>;

  return (
    <div className="panel__body">
      <div className="graph__bar">
        <span className="panel__note">
          {graph.placed.length} items · {graph.routed.length} relations · revision {data.revision}
        </span>
        <button type="button" className="button" onClick={fit}>
          Fit
        </button>
      </div>
      <p className="panel__note">F: {BADGE_MEANING}</p>
      {graph.dropped ? (
        <p className="panel__note">
          {graph.dropped} relation(s) point outside the current heads and are not drawn.
        </p>
      ) : null}
      <svg
        ref={frame}
        className="graph"
        role="img"
        aria-label="The project ledger as a graph. Drag to pan, scroll to zoom, click a node for its detail."
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
      >
        <defs>
          <marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8"
                  markerUnits="userSpaceOnUse" orient="auto">
            <path d="M0 0 L8 4 L0 8 z" className="edge__head" />
          </marker>
          <marker id="graph-arrow-stale" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8"
                  markerUnits="userSpaceOnUse" orient="auto">
            <path d="M0 0 L8 4 L0 8 z" className="edge__head edge__head--stale" />
          </marker>
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {graph.routed.map((edge) => (
            <path
              key={edge.id}
              d={path(edge.points)}
              className={`edge edge--${STROKE[edge.kind] ?? 'thin'}${edge.stale ? ' edge--stale' : ''}`}
              markerEnd={`url(#${edge.stale ? 'graph-arrow-stale' : 'graph-arrow'})`}
            >
              <title>{`${edge.kind}${edge.stale ? ' (stale)' : ''}`}</title>
            </path>
          ))}
          {graph.placed.map((node) => {
            const {x, y, width, height} = node.box;
            const formal = node.evidence.includes('formal');
            return (
              <g
                key={node.id}
                data-id={node.id}
                className={node.id === openId ? 'node node--open' : 'node'}
                transform={`translate(${x - width / 2} ${y - height / 2})`}
              >
                <rect className={`node__box node__box--${FAMILY[node.kind] ?? 'other'}`} width={width} height={height} rx="6" />
                <text className="node__label" x="10" y={height / 2 + 4}>
                  {fitted(node)}
                </text>
                {formal ? (
                  <g>
                    {/* First child, so this is the title the badge's own
                        hover resolves to rather than the node's. */}
                    <title>{BADGE_MEANING}</title>
                    <circle className="node__badge" cx={width - 11} cy="11" r="8" />
                    <text className="node__badge-text" x={width - 11} y="15" textAnchor="middle">
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
      {open ? <Drawer node={open} onClose={() => setOpenId('')} onDraft={onDraft} /> : null}
    </div>
  );
}
