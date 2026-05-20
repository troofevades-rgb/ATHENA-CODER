"""LSP diagnostics surface (T5-03R).

A minimal client that launches a configured language server,
opens documents, collects ``textDocument/publishDiagnostics``
notifications, and returns structured :class:`Diagnostic` records.

The :func:`diagnose` function is the seam both the user-facing
``Diagnose`` tool (athena/tools/diagnose.py) and the T5-04
verified-execution gate consume.
"""

from .client import Diagnostic, Severity, diagnose

__all__ = ["Diagnostic", "Severity", "diagnose"]
