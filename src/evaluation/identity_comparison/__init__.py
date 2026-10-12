"""Two ways of telling which mentions are one human, on the same mentions.

A is ER v2 (`persons.resolution`); B is the rules of entity grouping (`entities.grouping`)
without the model that names entities and without a person's decisions. Read-only: the
benchmark writes nothing into a database it compares, and changes no pipeline. See
`docs/architecture/identity-resolution-comparison.md`.

    python -m evaluation.identity_comparison --help
"""
