// Which component draws which page.
//
// A lookup rather than a switch, because the route is whatever the hash says
// and a page that is not registered here yet should be answered in words
// rather than by a blank panel or a throw: `Shell.jsx` draws "unknown route"
// for a route absent here, and `Sidebar.jsx` leaves out an entry whose page
// is absent (Workflows, until it lands). A route with no page is a different
// thing from a page with nothing in it.
//
// `chat` and `tree` are Task 13a: `Shell.jsx` treats both routes as the chat
// surface (mounted once, shown full width, no dock aside) -- `tree` is
// reached from its own sidebar entry and from the transcript/tree toggle in
// Chat's own header.
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
import Status from './Status.jsx';
import Tree from './Tree.jsx';
import Workflows from './Workflows.jsx';

export const PAGES = {
  home: Home,
  chat: Chat,
  tree: Tree,
  jobs: Jobs,
  library: Library,
  ledger: Ledger,
  workflows: Workflows,
  results: Results,
  runs: Runs,
  publications: Publications,
  checkpoints: Checkpoints,
  files: Files,
  status: Status,
  environment: Environment,
  help: Help,
};
