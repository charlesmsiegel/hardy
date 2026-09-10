"""Human command help, separate from model instructions and their prompt digest."""

PUBLICATION_USAGE = (
    "/project publish ITEM --scope SCOPE --output BUNDLE; "
    "/project link SOURCE illustrates|documents TARGET; "
    "/project mark ITEM internal|public|omitted"
)
PROJECT_USAGE = (
    "/project list · /project switch <name> · /project new <name> · "
    + PUBLICATION_USAGE
)
