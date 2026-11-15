from typing import Protocol


class NotificationChannel(Protocol):
    """Explicit opt-in notification integrations implement this interface."""

    async def send(self, alert: dict) -> None: ...
