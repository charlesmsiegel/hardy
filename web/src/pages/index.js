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

import Chat from './Chat.jsx';
import Environment from './Environment.jsx';
import Help from './Help.jsx';
import Home from './Home.jsx';
import Tree from './Tree.jsx';

export const PAGES = {
  home: Home,
  chat: Chat,
  tree: Tree,
  environment: Environment,
  help: Help,
};
