"""Reserved namespace for a future QQ adapter.

QQ transport code is intentionally not part of the active application. A
future implementation should satisfy :class:`core.channel.ChannelAdapter`.
"""

from core.channel import ChannelAdapter, MessageEvent

__all__ = ["ChannelAdapter", "MessageEvent"]
