// Which component draws which tab.
//
// A lookup rather than a switch, because thirteen tabs arrive across three
// tasks and a page that has not landed yet should draw its empty state rather
// than break the build. A tab whose id is absent here is a tab whose page does
// not exist, which is a different thing from a page with nothing in it -- so
// the fallback says so in words rather than rendering a blank panel.
//
// `chat` and `tree` are Task 13a: `Shell.jsx` treats both routes as the chat
// surface (mounted once, shown full width, no dock aside) -- `tree` has no
// tab of its own (`TabBar.jsx` lights the Chat tab for it too) and is
// reached only through the transcript/tree toggle in Chat's own header.
//
// `jobs`, `library` and `ledger` (its graph half; the list half shipped in
// `e074b9a`) are Task 13b. `files` (read-only; the editor is shipment 3) is
// Task 13c. `results` (Task 8) is the three-lane theorem table and detail.

import Chat from './Chat.jsx';
import Checkpoints from './Checkpoints.jsx';
import Environment from './Environment.jsx';
import Files from './Files.jsx';
import Help from './Help.jsx';
import Home from './Home.jsx';
import Jobs from './Jobs.jsx';
import Ledger from './Ledger.jsx';
import Library from './Library.jsx';
import Publications from './Publications.jsx';
import Results from './Results.jsx';
import Runs from './Runs.jsx';
import Tree from './Tree.jsx';

export const PAGES = {
  home: Home,
  chat: Chat,
  tree: Tree,
  jobs: Jobs,
  library: Library,
  ledger: Ledger,
  results: Results,
  runs: Runs,
  publications: Publications,
  checkpoints: Checkpoints,
  files: Files,
  environment: Environment,
  help: Help,
};
