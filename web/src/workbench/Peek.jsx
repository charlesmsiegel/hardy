// The peek popover: a 380px card placed near whatever was clicked to open
// it. Name, kind, source and "reads as" are whatever the caller supplied --
// this component draws them and decides nothing about where they came from.
//
// The Lean signature, the doc line and `where ->` are different: they come
// from a declaration-detail lookup this shipment does not have, so they are
// always `Absent` here, never a prop. That is not a stand-in for a future
// prop of the same name -- shipment 3, which adds the endpoint, changes
// what this component renders for them, and until then nothing this
// component could be handed for `sig`/`doc`/`where` would be honest.
//
// Placement is the point of the component. It renders once near the anchor
// to measure its own size, then places itself: below by default, flipped
// above when there is no room below, and clamped inside the viewport on
// both axes either way -- a popover that renders off-screen has failed at
// the one thing this component exists to do.

import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import Absent from '../components/Absent.jsx';

const WIDTH = 380;
const MARGIN = 12;
const GAP = 6;

function place(anchor, size) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = size.width || WIDTH;
  const height = size.height || 0;

  let top = anchor.y + GAP;
  // Flip above only when there is room above and not below -- flipping into
  // an edge that has even less room would trade one overflow for another.
  if (height && top + height > vh - MARGIN && anchor.y - height - GAP >= MARGIN) {
    top = anchor.y - height - GAP;
  }
  top = Math.min(Math.max(top, MARGIN), Math.max(MARGIN, vh - height - MARGIN));

  let left = anchor.x;
  left = Math.min(Math.max(left, MARGIN), Math.max(MARGIN, vw - width - MARGIN));

  return {top, left};
}

export default function Peek({info, onClose, go}) {
  const ref = useRef(null);
  const [pos, setPos] = useState(() => place({x: info.x, y: info.y}, {width: WIDTH, height: 0}));

  // Measured and placed before the browser paints, so the flip/clamp never
  // shows as a jump -- the first frame drawn is already the final position.
  useLayoutEffect(() => {
    const node = ref.current;
    const size = node ? {width: node.offsetWidth, height: node.offsetHeight} : {width: WIDTH, height: 0};
    setPos(place({x: info.x, y: info.y}, size));
  }, [info.x, info.y]);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === 'Escape') onClose();
    };
    const onDocDown = (event) => {
      if (ref.current && !ref.current.contains(event.target)) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDocDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDocDown);
    };
  }, [onClose]);

  return (
    <div ref={ref} className="wb-peek" style={{left: pos.left, top: pos.top, width: WIDTH}}>
      <div className="wb-peek__head">
        <span className="wb-peek__name">{info.name}</span>
        <span className="wb-peek__kind">{info.kind}</span>
        <span className="wb-peek__src">{info.source}</span>
        <a
          href="#"
          className="wb-peek__close"
          onClick={(event) => {
            event.preventDefault();
            onClose();
          }}
        >
          close
        </a>
      </div>
      <div className="wb-peek__grid">
        <span className="wb-peek__label">reads as</span>
        <span className="wb-peek__informal">{info.informal}</span>
        <span className="wb-peek__label">Lean</span>
        <pre className="wb-peek__sig">
          <Absent kind="unreported" />
        </pre>
        <span className="wb-peek__label">doc</span>
        <span>
          <Absent kind="unreported" />
        </span>
      </div>
      <div className="wb-peek__foot">
        <span className="wb-peek__where">
          <Absent kind="unreported" /> →
        </span>
        {info.ledger ? (
          <a
            href="#"
            onClick={(event) => {
              event.preventDefault();
              onClose();
              go?.({page: 'ledger', arg: info.ledger});
            }}
          >
            § {info.ledger}
          </a>
        ) : null}
      </div>
    </div>
  );
}
