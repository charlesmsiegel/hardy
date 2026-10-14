"""Human command help, separate from model instructions and their prompt digest."""

PUBLICATION_USAGE = (
    '/project target ID "INFORMAL" --formal "LEAN" [--context CONTEXT] [--supersedes ID@FULL_SHA256]; '
    "/project review TARGET@SHA FORMAL@SHA --verdict approved|disputed|unresolved --reviewer NAME --conditions TEXT --reason TEXT [--scope SCOPE@SHA]; "
    "/project bind TARGET@SHA FORMAL@SHA --saved SAVED@SHA [--scope SCOPE@SHA]; /project inspect TARGET; /project export TARGET --output NAME; "
    "/project publish ITEM --scope SCOPE --output BUNDLE; "
    "/project link SOURCE illustrates|documents TARGET; "
    "/project mark ITEM internal|public|omitted"
    '; /project research RUN TARGET@SHA --scope SCOPE@SHA [--operation prove|refute|compute]; '
    '/project explore RUN --action context|concept|representation --text TEXT; '
    '/project repair RUN OBLIGATION@SHA --original ARGUMENT@SHA; '
    '/project intake RUN --source PATH@SHA --scope-id ID [--citation-depth 0|1|2]; '
    '/project referee RUN --review REVIEW@SHA --scope SCOPE@SHA --main REF[,REF]; '
    '/project workflow-status [RUN]; /project intake-status [RUN]; /project referee-report [RUN]; '
    '/project catalog; /project action OPERATION JSON'
)
PROJECT_USAGE = (
    "/project list · /project switch <name> · /project new <name> · "
    + PUBLICATION_USAGE
)
