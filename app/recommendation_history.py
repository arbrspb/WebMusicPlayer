"""Session-only cooldown for already shown recommendation candidates.

This module deliberately does not persist anything in ``scan_results`` and
does not alter similarity scores.  It only reorders an already ranked pool.
"""
from __future__ import annotations

import os
import threading
import time


GLOBAL_UNIQUE_COOLDOWN = 40
PAIR_UNIQUE_COOLDOWN = 80
GLOBAL_TIME_COOLDOWN_SECONDS = 30 * 60
PAIR_TIME_COOLDOWN_SECONDS = 2 * 60 * 60


def _track_key(value):
    return os.fspath(value or "").strip().replace("\\", "/").casefold()


def _reference_key(references):
    return "\n".join(sorted({_track_key(value) for value in references or [] if _track_key(value)}))


class RecommendationHistory:
    """Small thread-safe registry for one running application process."""

    def __init__(self):
        self._lock = threading.RLock()
        self.clear()

    def clear(self):
        with getattr(self, "_lock", threading.RLock()):
            self._sequence = 0
            self._shown = {}
            self._pairs = {}

    def stats(self):
        with self._lock:
            return {"tracks": len(self._shown), "reference_pairs": len(self._pairs)}

    def _cooldown_rank(self, track_key, reference_key, now):
        global_entry = self._shown.get(track_key)
        pair_entry = self._pairs.get((reference_key, track_key)) if reference_key else None
        if pair_entry:
            age = self._sequence - pair_entry["sequence"]
            seconds = now - pair_entry["timestamp"]
            if age < PAIR_UNIQUE_COOLDOWN and seconds < PAIR_TIME_COOLDOWN_SECONDS:
                return 2, pair_entry["sequence"]
        if global_entry:
            age = self._sequence - global_entry["sequence"]
            seconds = now - global_entry["timestamp"]
            if age < GLOBAL_UNIQUE_COOLDOWN and seconds < GLOBAL_TIME_COOLDOWN_SECONDS:
                return 1, global_entry["sequence"]
        return 0, -1

    def rerank(self, candidates, references, limit, *, now=None, record=True, recommendation_type="intelligent"):
        """Prefer fresh candidates, then fall back to the oldest recent ones."""
        now = float(time.time() if now is None else now)
        reference_key = _reference_key(references)
        unique = []
        seen = set()
        for index, item in enumerate(candidates or []):
            key = _track_key((item or {}).get("path") or (item or {}).get("rel_path"))
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append((index, key, item))
        with self._lock:
            ranked = []
            for original_index, key, item in unique:
                cooldown_rank, last_sequence = self._cooldown_rank(key, reference_key, now)
                # Existing ranking is retained inside each freshness group.
                # For fallback groups, older recommendations are released first.
                fallback_age = last_sequence if cooldown_rank else 0
                ranked.append((cooldown_rank, fallback_age, original_index, key, item))
            ranked.sort(key=lambda row: (row[0], row[1], row[2]))
            selected = ranked[: max(1, int(limit or 1))]
            if record:
                for _rank, _age, _index, key, _item in selected:
                    self._sequence += 1
                    entry = self._shown.setdefault(key, {"count": 0})
                    entry.update({
                        "sequence": self._sequence, "timestamp": now,
                        "count": entry["count"] + 1,
                        "reference": reference_key,
                        "type": str(recommendation_type or "intelligent"),
                    })
                    if reference_key:
                        pair = self._pairs.setdefault((reference_key, key), {"count": 0})
                        pair.update({"sequence": self._sequence, "timestamp": now, "count": pair["count"] + 1})
            return [row[4] for row in selected]


recommendation_history = RecommendationHistory()
