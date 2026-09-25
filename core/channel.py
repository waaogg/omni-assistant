"""Stable channel contracts shared by active and future adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class MessageEvent:
    """Normalized inbound message independent of a transport protocol."""

    channel: str
    sender_id: str
    text: str
    conversation_id: str | None = None
    metadata: Mapping[str, Any] | None = None


class ChannelAdapter(Protocol):
    """Contract for a channel adapter.

    Adapters own transport authentication and delivery. Core services must
    only consume normalized events and return plain text responses.
    """

    channel_name: str

    async def run(self) -> None:
        """Run the adapter until cancelled."""

    async def send_text(
        self,
        recipient_id: str,
        text: str,
        *,
        conversation_id: str | None = None,
    ) -> None:
        """Send a plain-text response through the channel."""
