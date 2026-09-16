// The header bar Chat and Tree both draw, verbatim the same line in the
// prototype at both sites (`main · turn 14 running · 17 entries · 3 lines`),
// with the transcript/tree toggle at its right end. Presentational only --
// `chat/tree.js`'s `headerStats` computes the numbers, both pages already
// hold the `route`/`go` pair `useHash` gives them.

export default function Header({chatLabel, running, entries, lines, active, go}) {
  return (
    <div className="wb-chat-header">
      <strong>{chatLabel}</strong>
      {running ? <span>· {running}</span> : null}
      <span>
        · {entries} entries · {lines} lines
      </span>
      <span className="wb-chat-header__toggle">
        <button
          type="button"
          className={active !== 'tree' ? 'wb-viewtoggle wb-viewtoggle--on' : 'wb-viewtoggle'}
          onClick={() => go('chat')}
        >
          transcript
        </button>
        <button
          type="button"
          className={active === 'tree' ? 'wb-viewtoggle wb-viewtoggle--on' : 'wb-viewtoggle'}
          onClick={() => go('tree')}
        >
          tree
        </button>
      </span>
    </div>
  );
}
