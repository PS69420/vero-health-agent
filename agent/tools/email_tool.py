"""
Mock email connector.

Instead of sending real email (SMTP / Graph API / etc.), writes each drafted
email to output/emails/ as a plain .txt file and returns a record. Swap for a
real EmailTool implementation when ready to actually send to physicians --
same send() signature.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from agent.tools.base import EmailTool


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class MockEmailTool(EmailTool):
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def send(self, to: str, subject: str, body: str, patient_id: str, as_of: Optional[date] = None) -> dict:
        self._counter += 1
        # Timestamp against the business date being evaluated, not wall-clock
        # time, so a backtest replaying several historical `as_of` dates in
        # one process gets correctly-dated records (and correct cooldowns).
        sent_at = datetime.combine(as_of, datetime.now().time()) if as_of else datetime.now()
        message_id = f"MOCK-{sent_at.strftime('%Y%m%d%H%M%S')}-{self._counter:03d}"
        filename = f"{sent_at.strftime('%Y%m%d')}_{patient_id}_{_slugify(subject)[:50]}.txt"
        path = self.out_dir / filename
        with open(path, "w") as f:
            f.write(f"To: {to}\nSubject: {subject}\nMessage-Id: {message_id}\n\n{body}\n")
        return {
            "message_id": message_id,
            "to": to,
            "subject": subject,
            "sent_at": sent_at.isoformat(),
            "file": str(path),
            "simulated": True,
        }
