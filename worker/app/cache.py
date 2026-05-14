from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CachedCandidate:
    candidate: dict
    created_at: float


class CandidateCache:
    def __init__(self, ttl_seconds: int = 900):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, CachedCandidate] = {}

    def set(self, candidate_id: str, candidate: dict) -> None:
        self._items[candidate_id] = CachedCandidate(candidate=candidate, created_at=time.time())

    def get(self, candidate_id: str) -> dict | None:
        item = self._items.get(candidate_id)
        if item is None:
            return None
        if time.time() - item.created_at > self.ttl_seconds:
            self._items.pop(candidate_id, None)
            return None
        return item.candidate
