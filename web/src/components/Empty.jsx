// The one empty-state block every page's `empty` mode drops in below its own
// header -- a heading and one honest sentence, never an icon standing in for
// "there is nothing here yet." The sentence is the caller's to write (it
// names real, counted zeros), not this component's.

export default function Empty({title, line}) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      <div className="empty__line">{line}</div>
    </div>
  );
}
