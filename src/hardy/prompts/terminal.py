"""Human command help, separate from model instructions and their prompt digest."""

PUBLICATION_USAGE = (
    '/project target ID "INFORMAL" --formal "LEAN" [--context CONTEXT] [--supersedes ID@FULL_SHA256]; '
    "/project review TARGET@SHA FORMAL@SHA --verdict approved|disputed|unresolved --reviewer NAME --conditions TEXT --reason TEXT; "
    "/project bind TARGET@SHA FORMAL@SHA --saved SAVED@SHA; /project inspect TARGET; /project export TARGET --output NAME; "
    "/project publish ITEM --scope SCOPE --output BUNDLE; "
    "/project link SOURCE illustrates|documents TARGET; "
    "/project mark ITEM internal|public|omitted"
)
PROJECT_USAGE = (
    "/project list · /project switch <name> · /project new <name> · "
    + PUBLICATION_USAGE
)
