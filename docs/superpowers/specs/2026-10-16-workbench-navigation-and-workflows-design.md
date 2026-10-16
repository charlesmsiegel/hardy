# Hardy workbench, third pass: one navigation, a command palette, a Workflows page, and the missing controls

This file records the reasoning behind a redesign of the browser client and the
criteria an implementation must meet. Implementation status lives in
[the roadmap](../../roadmap.md). It builds on
[the workbench second pass](2026-09-15-workbench-second-pass-design.md) and
changes nothing that pass established about the session, the record or what
counts as evidence.

## 1. Why

The workbench is honest and complete in its reads, but a person who opens it
cannot find most of what Hardy does. An inventory of every slash command, CLI
command and HTTP route against the client found:

- **Three navigation systems.** Ten tabs, three small top-bar links (Status,
  Environment, Help) and a Tree page reachable only from inside Chat.
- **Every mathematical workflow buried.** Ledger draws eleven collapsed
  `<details>` forms above its own list, graph and empty state. A form's success
  or refusal is printed inside its collapsed section, so closing it hides the
  answer.
- **Controls the docs promise but the page lacks.** `session-commands.md` and
  `cli.md` describe a header model picker and click-to-compose on Help;
  `chat/ModelPicker.jsx` is never imported, and Help's commands are plain text.
  The server answers `POST /api/projects/<slug>/chats` and
  `PATCH /api/projects/<slug>/chats/<id>`; nothing calls either.
- **Actions reachable only by typing.** A running turn can be stopped only by
  Esc with the composer focused. Setting the goal, `/abandon`, `/export`,
  `/cas state|reset|export`, `/jobs pin|unpin|finish`, and the structured
  `obligation` operation have no control. Upload is drag-and-drop only: there is
  no file input anywhere in `web/src`.
- **Unstyled native controls and no keyboard path.** Form primitives in
  `workbench/ProjectWorkflow.jsx` carry inline styles; clickable table rows,
  file rows and tree rows have no role or focus; several inputs have a
  placeholder and no label.
- **Copy that has drifted from the code.** Help's keys block, Environment's
  "what /env prints" (the command is `/doctor`), the project switcher's
  `/project <name>` hint (the command is `/project switch <name>`), Library's
  empty state, and a pointer to a `docs/manual.md` that does not exist.

## 2. Goals

1. **One navigation model.** A grouped sidebar replaces the tab bar and the
   top-bar links. Every page, Tree included, has an entry. Counts that need
   attention (attention items on Jobs, cards waiting for an answer) show on the
   entries they concern.
2. **A command palette** on Ctrl+K / ⌘K: every page, every chat of the open
   project, every workflow form, and every command in `/api/commands`. Choosing
   a page or chat navigates; choosing a workflow opens its form; choosing a
   command puts `/<name> ` in the composer and focuses it. The palette never
   sends anything itself. The prototype's Help copy named this palette; it was
   never wired.
3. **A Workflows page** (`#/workflows`, `#/workflows/<form>`) that holds the
   eleven project forms plus a new obligation form, grouped by what a person is
   doing:
   - *Define:* semantic target, mathematical context, required work (scope).
   - *Bring in sources:* manuscript intake, mapping review.
   - *Investigate:* Research, Explore and Repair.
   - *Judge:* Referee, premise admission, statement and argument review.
   - *Record:* supplied arguments and revisions, obligations.

   The page lists the groups on the left and draws one form at a time on the
   right, with the capability status beside it. A form's feedback is always
   visible while that form is open. Ledger keeps its list, graph and item pane
   as its whole page, with a "New…" menu that links into Workflows.
4. **The missing controls**, each as sugar over a path that already exists,
   never a second path:
   - a **Stop** button whenever a turn or a command runs (`POST /api/cancel`,
     the same as Esc);
   - **New chat** and **Rename** on the Chat rail (the two unused routes);
   - the **model picker** in the Chat header (`ModelPicker.jsx`, submitting
     `/model <identity>` as the second-pass spec describes);
   - **clickable commands** on Help, putting the command in the composer;
   - **Set goal** on Home (submits `/goal <text>`);
   - **Abandon** on Tree (submits `/abandon` with its lesson) beside Fork;
   - an **Upload** button on Files (a file input posting to the same
     `/api/upload` the drop target uses);
   - Jobs actions offered only in states where they apply, and a reinforcement
     amount instead of a fixed +2;
   - the structured **obligation** form;
   - a **pending-card indicator** in the top bar that opens Chat, so an approval
     card is never visible only in a hidden dock.
5. **A control and layout system.** One set of classes for buttons (primary,
   secondary, quiet, danger), fields with visible labels, selects, text areas,
   disclosure sections, cards, and focusable list rows. Visible focus rings.
   Every clickable row is a button for the keyboard (Enter or Space activates
   it). An error boundary around the page body, so one page's exception draws
   an error panel instead of blanking the workbench.
6. **Copy that matches the code** for every drift item in §1.

## 3. Non-goals

- No new server routes, no change to any route's semantics, and no change to
  what any page reads. Everything in §2 uses routes that already exist.
- No change to the vocabulary of evidence: `Absent`'s rulings, `Pill`'s tone
  laws, the ⊢ / § / “ ” lanes, and every "not reported" stay exactly as they
  are. Making a page look finished by hiding an absence is the failure this
  design must not produce.
- No new npm dependency, no web font, no icon font.
- No CLI-only capability (evals, setup, accept, batch, latency, `doctor --deep`)
  moves into the browser in this pass.

## 4. Anti-goals

- A palette or button that sends a command a person did not see. Every command
  that changes state still goes through the composer, the existing confirmation
  cards, or a form with an explicit submit.
- A redesign that breaks the Content-Security-Policy: no inline `<script>`, no
  `data:` fonts or stylesheets, no off-host URL.
- A stale bundle. `src/hardy/app/web/static/` is rebuilt and committed with
  every source change, and a rebuild must reproduce it byte for byte (the
  `web-smoke` CI job checks this).

## 5. Constraints

- The strict CSP and the token meta described in `web/README.md`.
- `web/smoke.mjs` and the unit tests that read `web/src`
  (`tests/unit/test_web_absent.py` and the node tests in `package.json`) keep
  passing; the smoke's hash-router check keeps finding
  `window.addEventListener('hashchange', …)`.
- Routing stays in the URL hash (`session/useHash.js`). New routes are
  `#/workflows` and `#/workflows/<form>`; existing routes keep their meaning so
  links in transcripts and refusals still resolve.
- Pages keep their mounting rules from `Shell.jsx`: every page but Chat mounts
  only while active; Chat is mounted once and placed by the dock.
- Light and dark palettes both stay defined for every colour.
- `docs/reference/cli.md` and `session-commands.md` describe the browser; they
  change in the same commit as the page they describe.

## 6. Decisions

- **Sidebar groups:** *Project* (Home, Chat, Tree), *Mathematics* (Ledger,
  Workflows, Results, Publications), *Execution* (Jobs, Runs, Checkpoints),
  *Sources* (Library, Files), *Session* (Status, Environment, Help). The
  sidebar collapses to a narrow rail on demand and below 900px. The chat dock
  keeps its pinned / strip / hidden cycle.
- **Typography:** the serif stack stays for prose and the monospace stack for
  identifiers, code and figures; navigation, buttons, labels and table headers
  move to the system UI sans stack (`system-ui, -apple-system, "Segoe UI",
  Roboto, sans-serif`) for legibility at small sizes. Base size rises from
  13px to 14px. No font is downloaded.
- **Palette:** keep the paper-and-ink greens and both themes; raise text and
  border contrast; one focus-ring colour derived from `--accent`.
- **Delivery:** four stacked pull requests, each with its rebuilt bundle:
  (1) this document and the control and layout system, including the form
  primitives; (2) sidebar, palette, Stop, pending-card indicator and error
  boundary; (3) the Workflows page, the obligation form and the Ledger
  clean-up; (4) the remaining controls of §2.4 and the copy fixes of §1.

## 7. Left to the builder

Exact spacing and colour values, icon glyphs (Unicode, never a font), the
palette's ranking of matches, the sidebar's collapse affordance, and how each
form lays out its fields within the shared primitives.
