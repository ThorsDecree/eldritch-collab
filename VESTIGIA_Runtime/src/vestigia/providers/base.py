from __future__ import annotations

from typing import Protocol

from ..models import ProviderReply, ProviderRequest


class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderReply:
        ...

