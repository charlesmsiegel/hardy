// Reshaping `/api/tree`'s flat `{entry_id, parent_id, type, lesson}` list into
// a git-style lane layout, shared by the Chat page's header counts and the
// Tree page's graph.
//
// `panels.session.tree()` gives one honest fact per entry and nothing more --
// no timestamp, no per-entry text, no model/token accounting. Everything this
// module derives (which entry is a fork, which is an abandon, which lane an
// entry sits in, which entries are leaves) is a computation over that exact
// data, never a value invented to fill a gap. The one built-in rule the
// server's own schema guarantees and this leans on: a `conversation_branch`
// entry carries a non-empty `lesson` if and only if it is an `abandon` (never
// a `fork`) -- `history.py`'s `validate` rejects any other combination, so
// reading the action back off `lesson`'s presence is exact, not a guess.

/** A branch-transition entry that recorded a human lesson: an abandon. */
export function isAbandon(entry) {
  return entry.type === 'conversation_branch' && Boolean(entry.lesson);
}

/** A branch-transition entry with no lesson: a fork. */
export function isFork(entry) {
  return entry.type === 'conversation_branch' && !entry.lesson;
}

/** Entries nothing else names as a parent -- the tip of every line, active or not. */
export function leafIds(entries) {
  const parents = new Set(entries.map((entry) => entry.parent_id).filter((id) => id !== null && id !== undefined));
  return entries.filter((entry) => !parents.has(entry.entry_id)).map((entry) => entry.entry_id);
}

/** How many entries name `id` as their parent. */
export function childCount(entries, id) {
  return entries.filter((entry) => entry.parent_id === id).length;
}

/**
 * Assign every entry a lane (column) and a row (its position in `entries`,
 * which is already oldest-first: `History.append` builds the dict in the
 * order events were written, and Python/JS both keep insertion order).
 *
 * The rule is the ordinary git-log one: an entry continues its parent's lane
 * when the parent is still that lane's open tip; otherwise it opens a new
 * lane, one row taller than any used so far. A parent gets two children
 * exactly when something forked from it a second time (or forked from an
 * ancestor whose line had already moved on) -- the first child continues the
 * lane in place, so the second necessarily finds the lane's tip already
 * moved past its parent and opens its own. Lanes are never recycled: an
 * abandoned or otherwise finished lane's column stays its own for the life of
 * the tree, which trades some horizontal space for never reassigning a
 * column a reader has already read.
 */
export function layout(entries) {
  const lanes = []; // lanes[i] = the entry_id currently at that lane's tip
  const laneOf = new Map();
  const rowOf = new Map();
  const rows = entries.map((entry, row) => {
    let lane = entry.parent_id === null || entry.parent_id === undefined ? -1 : lanes.indexOf(entry.parent_id);
    if (lane === -1) {
      lane = lanes.length;
      lanes.push(entry.entry_id);
    } else {
      lanes[lane] = entry.entry_id;
    }
    laneOf.set(entry.entry_id, lane);
    rowOf.set(entry.entry_id, row);
    return {entry, lane, row};
  });

  const edges = rows
    .filter(({entry}) => entry.parent_id !== null && entry.parent_id !== undefined && laneOf.has(entry.parent_id))
    .map(({entry, lane, row}) => ({
      fromLane: laneOf.get(entry.parent_id),
      fromRow: rowOf.get(entry.parent_id),
      toLane: lane,
      toRow: row,
      curved: laneOf.get(entry.parent_id) !== lane,
    }));

  // The entry each lane ended on, for colouring the lane by how its line
  // ended: rows are processed oldest-first, so the last write per lane index
  // is that lane's final tip.
  const laneTip = new Map();
  for (const row of rows) laneTip.set(row.lane, row.entry);

  return {rows, edges, laneCount: lanes.length, laneOf, rowOf, laneTip};
}

/**
 * The header bar Chat and Tree draw identically (`main · turn 14 running ·
 * 17 entries · 3 lines`), computed from data both pages already fetch.
 *
 * `running` names the turn ordinal, not the literal prototype figure: no
 * endpoint carries a turn index or a timestamp (`panels.session.transcript`
 * has neither field), but every `user`/`hardy` message really did start a
 * turn, so counting them is exact rather than invented. `entries`/`lines`
 * come straight off `/api/tree`: every node in the whole conversation, and
 * how many of them are leaves (a line's open tip).
 */
export function headerStats({messages, status, runningTool, treeData}) {
  // `startsTurn !== false` is the same predicate `withSeparators` uses, and it
  // has to be: a Hardy note (a project switch, an editor save) arrives as a
  // `hardy` message that starts no turn, so counting it unconditionally made
  // the header read `turn 2 running` over a transcript labelling the same
  // turn 1.
  const turnOrdinal = messages.filter(
    (message) =>
      (message.kind === 'user' || message.kind === 'hardy') && message.startsTurn !== false,
  ).length;
  const running = status.turn_running
    ? `turn ${turnOrdinal} running`
    : status.command_running
      ? runningTool
        ? `running ${runningTool}`
        : 'running'
      : '';
  return {
    running,
    entries: treeData.entries.length,
    lines: leafIds(treeData.entries).length,
  };
}
