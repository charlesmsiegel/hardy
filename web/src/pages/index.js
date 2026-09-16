// Which component draws which tab.
//
// A lookup rather than a switch, because thirteen tabs arrive across three
// tasks and a page that has not landed yet should draw its empty state rather
// than break the build. A tab whose id is absent here is a tab whose page does
// not exist, which is a different thing from a page with nothing in it -- so
// the fallback says so in words rather than rendering a blank panel.

export const PAGES = {};
