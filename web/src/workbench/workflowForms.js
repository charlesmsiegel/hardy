// Navigation metadata for implemented operations. The page, Ledger's New
// menu and the palette share these identities; adding a descriptor requires
// its real form to be mounted by Workflows, never an inert placeholder.
export const WORKFLOW_GROUPS = [
  {id: 'define', label: 'Define'},
  {id: 'sources', label: 'Bring in sources'},
  {id: 'investigate', label: 'Investigate'},
  {id: 'judge', label: 'Judge'},
  {id: 'record', label: 'Record'},
];

export const WORKFLOW_FORMS = [
  {id: 'target', group: 'define', title: 'Create semantic target', summary: 'Record the intended statement and its proposed Lean version.'},
  {id: 'context', group: 'define', title: 'Create or revise mathematical context', summary: 'Contexts retain exact declarations, local hypotheses and representation conventions.'},
  {id: 'scope', group: 'define', title: 'Select or extend required work', summary: 'Extending a scope preserves its required items.'},
  {id: 'intake', group: 'sources', title: 'Import manuscript and propose source mappings', summary: 'Save a TeX source through Files or retain pasted manuscript text, then refresh source choices.'},
  {id: 'mappings', group: 'sources', title: 'Review manuscript mappings and required results', summary: 'Run manuscript intake, then select its exact proposal for review.'},
  {id: 'live', group: 'investigate', title: 'Research, Explore and Repair', summary: 'Investigate exact project items and retain the operation in workflow history.'},
  {id: 'referee', group: 'judge', title: 'Referee · main results, supplied arguments and repairs', summary: 'Approve the exact source mappings and select required results before running Referee.'},
  {id: 'admission', group: 'judge', title: 'Premise admission and exact scope decisions', summary: 'Required targets and novel manuscript results remain protected.'},
  {id: 'review', group: 'judge', title: 'Source, statement, proof step or argument review', summary: 'Record an attributed judgment of exact subjects, scopes and source passages.'},
  {id: 'arguments', group: 'record', title: 'Record supplied arguments, steps, critiques and revisions', summary: 'Record original supplied arguments or distinct repairs as exact versions.'},
  {id: 'obligations', group: 'record', title: 'Obligations · work and history', summary: 'Review outstanding obligations or filter the recorded work by status.'},
  {id: 'obligation-new', group: 'record', title: 'Open an obligation', summary: 'Record required work against an exact subject and scope, with its kind and reason.'},
];
