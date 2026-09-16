// The session's own `/summary`, drawn rather than printed.
//
// The shape is the terminal's: a goal, then the sections the session chose,
// then the obligations it is still carrying. Nothing is computed here -- a
// second opinion about what a section means would be a second answer to a
// question the session has already answered.

import usePanel from '../session/usePanel.js';

export default function Summary({revision}) {
  const {data, error} = usePanel('/api/summary', revision);

  if (error) return <p className="panel__error">{error}</p>;
  if (!data) return <p className="panel__note">Reading the summary...</p>;

  return (
    <div className="panel__body">
      <h2 className="panel__heading">Goal</h2>
      <p className="panel__goal">{data.goal || 'No goal set. Say /goal to state one.'}</p>

      {data.sections.map((section) => (
        <section key={section.title}>
          <h3 className="panel__subheading">{section.title}</h3>
          {section.lines.length ? (
            <ul className="panel__lines">
              {section.lines.map((line, index) => (
                <li key={`${section.title}-${index}`}>{line}</li>
              ))}
            </ul>
          ) : (
            <p className="panel__note">nothing yet</p>
          )}
        </section>
      ))}

      <h3 className="panel__subheading">Obligations</h3>
      {data.obligations.length ? (
        <ul className="panel__lines">
          {data.obligations.map((line, index) => (
            <li key={`obligation-${index}`}>{line}</li>
          ))}
        </ul>
      ) : (
        <p className="panel__note">none open</p>
      )}

      {/* The session distinguishes "nothing saved" from "nothing to say", and
          so does this: a panel that drew an empty Theorems section without it
          would read as a project with no results rather than one with none
          yet. */}
      {data.has_theorems ? null : <p className="panel__note">No theorem has been saved in this project yet.</p>}
    </div>
  );
}
