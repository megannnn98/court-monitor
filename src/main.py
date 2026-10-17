"""`python src/main.py <command>`, as before the workspace split: the operational scripts
and habits that start the command line by this path keep working.

Only a launcher. The command line is the installed application's (`court-monitor`,
`apps/court-monitor/src/main.py`); nothing is imported from this directory, and no
code may be added to it (`tests/test_package_graph.py`).
"""

from cli.app import main

if __name__ == "__main__":
    main()
