// The message list. One shape per role, and the open prompt cards at the end
// of it, because a gate is a thing the conversation is waiting on rather than
// a dialog over the top of it.

import {memo, useEffect, useRef} from 'react';
import {render} from './markdown.js';
import Prompt from './Prompt.jsx';

function Tools({tools}) {
  if (!tools.length) return null;
  return (
    <div className="tools">
      {tools.map((tool, index) => (
        <span
          key={`${tool.call_id || tool.name}-${index}`}
          className={`chip chip--${tool.ok === null ? 'running' : tool.ok ? 'ok' : 'bad'}`}
        >
          {tool.name || 'tool'}
          {tool.ok === null ? ' ...' : tool.ok ? ' ok' : ' failed'}
        </span>
      ))}
    </div>
  );
}

function Assistant({message}) {
  return (
    <article className="message message--assistant">
      {message.thinking ? <div className="thinking">thinking<span className="dot" /></div> : null}
      {message.thought ? <details className="thought"><summary>thought</summary><pre>{message.thought}</pre></details> : null}
      <Tools tools={message.tools} />
      {/* `render` drops every raw tag in the reply and leaves only markdown
          and KaTeX's own markup, which is what makes this assignment safe. */}
      <div className="prose" dangerouslySetInnerHTML={{__html: render(message.text)}} />
      {message.partial ? <div className="message__flag">interrupted</div> : null}
    </article>
  );
}

// Memoised on the message object, which the reducer replaces rather than
// mutates: only the one message a streaming token touched is re-rendered, so
// a long transcript is not re-parsed as markdown on every arriving word.
const Message = memo(function Message({message}) {
  switch (message.kind) {
    case 'user':
      return <article className="message message--user">{message.text}</article>;
    case 'hardy':
      // A turn Hardy started on the model's behalf, to carry background
      // results in: Hardy's line, never drawn as one the person typed.
      return <article className="message message--hardy">{message.text}</article>;
    case 'assistant':
      return <Assistant message={message} />;
    case 'tool':
      return (
        <article className={`message message--tool message--tool-${message.ok === false ? 'bad' : 'ok'}`}>
          <div className="message__label">{message.name || 'tool'}</div>
          <pre>{message.text}</pre>
        </article>
      );
    case 'system':
      return <article className={`message message--system message--${message.style}`}>{message.text}</article>;
    case 'notice':
      return <article className="message message--notice">{message.text}</article>;
    case 'turn':
      return <article className="message message--turn">{message.text}</article>;
    default:
      return null;
  }
});

export default function Chat({messages, prompts, loaded, onAnswer}) {
  const end = useRef(null);
  const length = messages.length;
  const open = prompts.length;

  // Follow the tail. Every new message and every prompt scrolls it into view;
  // a page that streams a reply the reader cannot see is a page that streams
  // nothing.
  useEffect(() => {
    end.current?.scrollIntoView({block: 'end'});
  }, [length, open]);

  return (
    <div className="chat">
      {!loaded && !length ? <div className="chat__empty">Loading the session...</div> : null}
      {loaded && !length && !open ? <div className="chat__empty">Nothing yet. Say something, or run /help.</div> : null}
      {messages.map((message) => (
        <Message key={message.id} message={message} />
      ))}
      {prompts.map((prompt) => (
        <Prompt key={prompt.id} prompt={prompt} onAnswer={onAnswer} />
      ))}
      <div ref={end} />
    </div>
  );
}
