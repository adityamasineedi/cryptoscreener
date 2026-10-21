"""Where rendered alerts go: the dry-run preview file, or Telegram through the app's existing sender.

Telegram delivery reuses ``app.services.telegram_alerts.TelegramAlertSubscriber`` (its credentials,
API base and timeout); the only addition is a reply-capable send that returns the message id, so
follow-ups can reply to the original alert. The token and chat id are never logged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app.services.telegram_alerts import TelegramAlertSubscriber


class _NoFeed:
    """The alert sender is not an AlertFeed subscriber."""

    def subscribe(self, *_a: Any) -> None:
        return None

    def unsubscribe(self, *_a: Any) -> None:
        return None


class ReplyingTelegramSender(TelegramAlertSubscriber):
    """The existing Telegram sender plus ``send(text, reply_to)`` returning the message id."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        timeout: float,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        super().__init__(bot_token=bot_token, chat_id=chat_id, feed=_NoFeed(), timeout=timeout)  # type: ignore[arg-type]
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=self.timeout))

    def send(self, text: str, reply_to: int | None = None) -> int | None:
        if not self.enabled or not text:
            return None
        body: dict[str, Any] = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        if reply_to is not None:
            body["reply_to_message_id"] = int(reply_to)
            body["allow_sending_without_reply"] = True
        try:
            with self._client_factory() as client:
                resp = client.post(f"{self.api_base}/bot{self.bot_token}/sendMessage", json=body)
        except Exception:  # noqa: BLE001 - the error text can carry the URL (token); never surface it
            return None
        if resp.status_code >= 400:
            return None
        try:
            return int(resp.json()["result"]["message_id"])
        except (ValueError, KeyError, TypeError):
            return None


class PreviewWriter:
    """Dry run: append each rendered message to ``preview.md``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, header: str, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"### {header}\n\n```\n{text}\n```\n\n")

    def note(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"{text}\n\n")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
