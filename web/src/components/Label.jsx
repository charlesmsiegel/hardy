// The section-label look -- 600 10px mono uppercase .08em muted -- is
// already defined once in styles.css as `.section-label` (Task 7). This
// just gives it a component name so pages reach for `<Label>` instead of
// restating the class, the way they reach for `<Pill>` instead of a border
// colour.

export default function Label({children}) {
  return <span className="section-label">{children}</span>;
}
