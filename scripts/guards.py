"""Small guards shared by the scripts that cost money or touch AWS."""

from __future__ import annotations

import os
import re
import sys


def require_paid(what: str) -> None:
    """Commands that call Amazon Bedrock (or otherwise incur charges) need ALLOW_PAID=1."""
    if os.environ.get("ALLOW_PAID") != "1":
        sys.exit(f"{what} calls paid AWS services (Amazon Bedrock model invocations). "
                 "Re-run with ALLOW_PAID=1 to confirm.")


_ACCOUNT = re.compile(r"(?<!\d)\d{12}(?!\d)")


def mask_text(text: str) -> str:
    """Replace 12-digit numbers (account ids) before anything is printed or written."""
    return _ACCOUNT.sub("111122223333", text)
