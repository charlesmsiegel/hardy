// The chat dock: `Shell`'s one and only mount of the Chat page, shown either
// as a 380px pinned aside beside another page or, on the Chat route itself,
// full width in the body's only column.
//
// `content` is built once by `Shell` and handed down; this component never
// constructs a second `<Chat/>`. The one thing this file cannot do, even
// while switching between the docked and full-width shapes, is change the
// element `content` is mounted under -- React remounts a subtree when its
// parent element's type changes, and a remounted Chat is a second instance
// in every sense that matters (its transcript subscription restarts, its
// scroll position is lost). So the root stays `<aside>` and `content` stays
// under the same `.wb-dock__body` wrapper in both shapes; only the header
// above it and a couple of classes/styles move.
//
// The aside also stays in the tree, merely hidden, when the dock is
// collapsed to the strip or hidden outright, for the same reason: an
// unmounted dock is an unmounted Chat. Below 900px it is always suppressed
// in favour of the footer strip, and the chat-page shape is immune to that
// -- both rules live in `styles.css`.
//
// `hidden` is the third case Task 13a adds: the Tree route. Tree wants the
// same full-width, no-aside layout as Chat (`Shell.jsx`'s `showAside`
// already leaves it no grid column to sit in), but it is not itself the
// chat-full shape -- Tree is its own page occupying that space, so the dock
// is not drawn at all there rather than shown full-bleed over Tree's own
// content. Chat still has to stay mounted underneath for when the toggle
// flips back, which is exactly what `display:none` gives for free.

export default function Dock({isChatPage, hidden, dock, chatLabel, running, queued, content, go}) {
  const docked = !isChatPage;

  return (
    <aside
      className={isChatPage ? 'wb-dock wb-dock--chat-full' : 'wb-dock'}
      style={isChatPage ? undefined : {display: !hidden && dock === 'pinned' ? 'grid' : 'none'}}
    >
      {docked ? (
        <div className="wb-dock__head">
          <strong>{chatLabel}</strong>
          {running ? (
            <span className="wb-dock__running">
              <span className="wb-turn__dot" />
              {running}
            </span>
          ) : null}
          {queued ? <span className="wb-dock__queued">{queued} queued</span> : null}
          <a
            className="wb-dock__open"
            href="#/chat"
            onClick={(event) => {
              event.preventDefault();
              go('chat');
            }}
          >
            {'open ↗'}
          </a>
        </div>
      ) : null}
      <div className="wb-dock__body">{content}</div>
    </aside>
  );
}
