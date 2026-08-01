from __future__ import annotations

from collections import deque

from ..models import ProviderReply, ProviderRequest


class FakeProvider:
    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = deque(replies or [])
        self.requests: list[ProviderRequest] = []

    def complete(self, request: ProviderRequest) -> ProviderReply:
        self.requests.append(request)
        if self.replies:
            text = self.replies.popleft()
        else:
            user = next(
                (item["content"] for item in reversed(request.messages) if item["role"] == "user"),
                "",
            )
            text = f"[fake-provider] Received: {user}"
        return ProviderReply(
            text=text,
            provider="fake",
            model=f"fake-{request.model_route}",
            response_id=f"fake-{request.turn_id}",
            usage={"input_messages": len(request.messages)},
        )

