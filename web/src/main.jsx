import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import SessionProvider from './session/SessionProvider.jsx';
import Shell from './workbench/Shell.jsx';
import './styles.css';
import 'katex/dist/katex.min.css';

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <SessionProvider>
      <Shell />
    </SessionProvider>
  </StrictMode>,
);
