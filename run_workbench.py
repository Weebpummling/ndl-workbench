"""Packaging entry point.

PyInstaller must be pointed at this file rather than at ndl_workbench/__main__.py.
Freezing the module file directly runs it as a top-level script with no package
context, and every `from . import ...` inside the package then fails at startup.
Importing the package here keeps the relative imports intact.
"""

import sys

from ndl_workbench.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
