"""``python -m otto.onboard <service>`` — standalone entry for the onboarding CLI."""

from __future__ import annotations

import sys

from otto.onboard.cli import main

if __name__ == "__main__":
    sys.exit(main())
