"""Self-update (T6-07).

``athena update`` detects how athena was installed, checks the
latest published release, previews what's changing, verifies
integrity, and installs — without ever hot-swapping the
running process. Offline-safe; opt-in startup notice.

Build order:

  detect.py   install-method detection (T6-07.1)
  check.py    latest-version lookup + changelog preview (T6-07.2)
  apply.py    install / pin / rollback per method (T6-07.3)
  athena/commands/update.py
              the CLI command + auto-check notice (T6-07.4)
"""

from .detect import InstallMethod, detect

__all__ = ["InstallMethod", "detect"]
