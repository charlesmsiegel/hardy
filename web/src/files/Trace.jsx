// The Mathlib trace view: real source, at its real lines, for the revision
// the project is built against.
//
// The line numbers are the whole point, so they come from the server rather
// than from counting the excerpt. `/api/declaration` answers an excerpt as
// `{from, text}`: `from` is the file's own line number for the first line of
// `text`. Numbering the block from 1 would put `Sylow.card_modEq_one` at line
// 9 of Mathlib, which is worse than showing no numbers -- it is a false
// statement made with the full confidence of a line number.
//
// The breadcrumb is the trail of names followed, because following one name
// into another is how reading Mathlib actually goes, and getting back to
// where you started should not mean retyping the name you came from.

import Absent from '../components/Absent.jsx';
import Label from '../components/Label.jsx';

export default function Trace({trail, revision, onFollow, onBack, onClose}) {
  const current = trail[trail.length - 1];
  if (!current) return null;
  const excerpt = current.excerpt;
  const lines = excerpt ? excerpt.text.split('\n') : [];
  const width = String((excerpt?.from ?? 1) + lines.length).length;

  return (
    <div className="wb-trace">
      <div className="wb-trace__bar">
        <span className="panel__note">Mathlib source {'·'} read-only</span>
        <span className="wb-trace__crumbs">
          {trail.map((step, index) => (
            <span key={`${step.name}-${index}`}>
              {index > 0 ? <span className="wb-trace__sep">{'›'}</span> : null}
              <button
                type="button"
                className={index === trail.length - 1 ? 'wb-trace__crumb wb-trace__crumb--on' : 'wb-trace__crumb'}
                onClick={() => onBack(index)}
              >
                {step.name}
              </button>
            </span>
          ))}
        </span>
        <span className="wb-editor__spacer" />
        <button type="button" className="wb-editor__button" onClick={onClose}>
          Back to the editor
        </button>
      </div>

      <div className="wb-trace__where">
        <span className="wb-rail__key">module</span>
        <span className="wb-rail__value">{current.module}</span>
        <span className="wb-rail__key">file</span>
        <span className="wb-rail__value">{current.source_path || <Absent kind="unreported" />}</span>
        <span className="wb-rail__key">revision</span>
        <span className="wb-rail__value">
          {revision || (
            <>
              <Absent kind="unreported" />{' '}
              <span className="panel__note">
                nothing this session serves states the revision of the packages on disk
              </span>
            </>
          )}
        </span>
      </div>

      {!excerpt ? (
        <div className="panel__note">
          <Absent kind="unreported" /> {'·'} the index holds {current.name} at line {current.line} of{' '}
          {current.module}, but that module resolved to no file on disk, so there is no source to show. Nothing
          nearby is shown in its place.
        </div>
      ) : (
        <>
          <Label>
            {current.module} {'·'} lines {excerpt.from}–{excerpt.from + lines.length - 1}
          </Label>
          <pre className="wb-trace__source">
            {lines.map((line, index) => {
              const number = excerpt.from + index;
              return (
                <div
                  key={number}
                  className={number === current.line ? 'wb-trace__line wb-trace__line--on' : 'wb-trace__line'}
                >
                  <span className="wb-trace__no">{String(number).padStart(width, ' ')}</span>
                  <span className="wb-trace__code">{line}</span>
                </div>
              );
            })}
          </pre>
          <div className="panel__note">
            Click a name in this source to follow it. Proofs are shown as Mathlib writes them; nothing here is
            evidence for this project, and nothing here is copied into it.
          </div>
          <div className="wb-trace__follow">
            {[...new Set((excerpt.text.match(/[A-Z][A-Za-z0-9_']*(?:\.[A-Za-z0-9_']+)*/g) || []))]
              .filter((name) => name !== current.name)
              .slice(0, 24)
              .map((name) => (
                <button key={name} type="button" className="wb-trace__name" onClick={() => onFollow(name)}>
                  {name}
                </button>
              ))}
          </div>
        </>
      )}
    </div>
  );
}
