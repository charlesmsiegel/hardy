// The one key/value grid the client uses for "kernel says" / "record says"
// panels, probe results, obligation details -- anywhere a fixed list of
// labelled facts needs to line up without a table. `rows` holds nodes, not
// strings, on purpose: a value is as often a <Pill/>, an <Absent/>, or a
// link as it is plain text, and this component draws whatever it is handed
// rather than deciding what a fact should look like.

import {Fragment} from 'react';

export default function Facts({rows, mono = false}) {
  return (
    <div className={mono ? 'facts facts--mono' : 'facts'}>
      {rows.map(([label, value], i) => (
        <Fragment key={i}>
          <span className="facts__label">{label}</span>
          <span className="facts__value">{value}</span>
        </Fragment>
      ))}
    </div>
  );
}
