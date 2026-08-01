from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from .db import ContinuityDB
from .models import MemoryRecord, RetrievedMemory


TYPE_AUTHORITY = {
    "identity": 5.0,
    "commitment": 4.7,
    "boundary": 4.7,
    "protocol": 4.4,
    "relationship": 4.1,
    "tension": 3.8,
    "symbol": 3.3,
    "place": 3.0,
    "preference": 2.7,
    "event": 2.0,
    "external_claim": 1.2,
    "interpretation": 1.0,
    "session_summary": 0.5,
    "other": 1.5,
}

AUTHORITY_WEIGHT = {
    "resident_accepted": 3.0,
    "resident_stated": 2.5,
    "participant_stated": 2.0,
    "inherited_unreviewed": 0.0,
    "unresolved": -0.2,
    "model_inferred": -1.0,
    "external": -1.0,
}

HALF_LIFE_DAYS = {
    "identity": None,
    "commitment": None,
    "boundary": None,
    "protocol": None,
    "relationship": 730.0,
    "preference": 365.0,
    "event": 180.0,
    "tension": 365.0,
    "external_claim": 30.0,
    "interpretation": 120.0,
    "session_summary": 14.0,
    "symbol": 730.0,
    "place": 730.0,
    "other": 180.0,
}


class Retriever:
    def __init__(self, db: ContinuityDB) -> None:
        self.db = db

    def retrieve(
        self,
        query: str,
        *,
        resident_id: str,
        room_id: str,
        limit: int = 18,
        include_inherited: bool = False,
        include_cold: bool = False,
    ) -> list[RetrievedMemory]:
        statuses = ["accepted"]
        if include_inherited:
            statuses.append("inherited_unreviewed")
        tiers = ["core", "hot", "warm"]
        if include_cold:
            tiers.append("cold")
        candidates = self.db.list_memories(
            resident_id=resident_id,
            room_id=room_id,
            statuses=statuses,
            tiers=tiers,
            limit=max(200, limit * 10),
        )
        fts_query = self._fts_query(query)
        ranks = self.db.search_fts(fts_query, limit=max(200, limit * 10)) if fts_query else {}
        lowered = query.casefold()
        now = datetime.now(UTC)
        results: list[RetrievedMemory] = []
        for record in candidates:
            if record.privacy == "sealed":
                continue
            if record.expires_at and record.expires_at < now.isoformat():
                continue
            topical, topical_reason = self._topical_score(record, lowered, ranks)
            if topical <= 0 and record.tier != "core":
                continue
            reasons = [topical_reason]
            type_score = TYPE_AUTHORITY.get(record.memory_type, 1.0)
            authority_score = AUTHORITY_WEIGHT.get(record.authority_state, 0.0)
            status_score = 0.6 if record.status == "accepted" else -0.5
            tier_score = {"core": 1.5, "hot": 1.0, "warm": 0.0, "cold": -1.0}.get(record.tier, 0.0)
            recency = self._recency(record, now)
            score = topical + type_score + authority_score + status_score + tier_score + recency
            reasons.extend(
                (
                    f"type_authority={type_score:.2f}",
                    f"source_authority={authority_score:.2f}",
                    f"recency={recency:.2f}",
                    f"tier={tier_score:.2f}",
                )
            )
            results.append(RetrievedMemory(record=record, score=score, reasons=tuple(reasons)))
        results.sort(key=lambda item: (-item.score, item.record.id))
        return results[: max(1, int(limit))]

    @staticmethod
    def _fts_query(query: str) -> str:
        words = re.findall(r"[A-Za-z0-9_#-]{2,}", query)
        unique = list(dict.fromkeys(word.casefold() for word in words))[:24]
        return " OR ".join(f'"{word}"' for word in unique)

    @staticmethod
    def _topical_score(
        record: MemoryRecord,
        lowered_query: str,
        ranks: dict[str, float],
    ) -> tuple[float, str]:
        score = 0.0
        reasons: list[str] = []
        if record.id in ranks:
            rank = abs(ranks[record.id])
            fts = 4.0 / (1.0 + rank)
            score += fts
            reasons.append(f"fts={fts:.2f}")
        symbolic_hits = [
            symbol
            for symbol in (*record.tags, *record.glyphs)
            if symbol and symbol.casefold() in lowered_query
        ]
        if symbolic_hits:
            symbolic = min(4.0, 1.5 * len(symbolic_hits))
            score += symbolic
            reasons.append(f"symbolic={symbolic:.2f}")
        content_words = set(re.findall(r"\w{3,}", record.content.casefold()))
        query_words = set(re.findall(r"\w{3,}", lowered_query))
        overlap = len(content_words & query_words)
        if overlap:
            lexical = min(3.0, overlap * 0.45)
            score += lexical
            reasons.append(f"lexical={lexical:.2f}")
        return score, ",".join(reasons) or "pinned-core"

    @staticmethod
    def _recency(record: MemoryRecord, now: datetime) -> float:
        half_life = HALF_LIFE_DAYS.get(record.memory_type, 180.0)
        if half_life is None:
            return 1.0
        try:
            created = datetime.fromisoformat(record.created_at)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
        except ValueError:
            return 0.0
        return math.exp(-math.log(2.0) * age_days / half_life)

