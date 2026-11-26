"""Near-Match Queue and Human Adjudication Workflow (DATA-025 / SEC-052).

Manages candidate pairs flagged by M1 Fellegi-Sunter between match and non-match thresholds.
Tracks reviewer actions (LINK, VOID, END_DATE, DECOUPLE) with immutable audit logging.
Once a human confirms a LINK, the relationship becomes a verified fact for Layer 1 and M3.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from ..models.results import MatchResult


class AdjudicationAction(str, Enum):
    LINK = "LINK"          # Human confirms match; entities are consolidated/linked
    VOID = "VOID"          # One entity voided as duplicate or fraudulent
    END_DATE = "END_DATE"  # Prior entity enrollment end-dated
    DECOUPLE = "DECOUPLE"  # Legitimate coincidental near-match; keep distinct


@dataclass
class QueueItem:
    """An item waiting in the near-match human adjudication queue."""
    item_id: str
    match_result: MatchResult
    enqueued_timestamp: str
    status: str = "PENDING"  # "PENDING", "ADJUDICATED"
    adjudicated_action: Optional[AdjudicationAction] = None
    adjudicated_by: Optional[str] = None
    adjudication_timestamp: Optional[str] = None
    reviewer_notes: Optional[str] = None


class NearMatchQueue:
    """In-memory or persistent store for Near-Match queue items."""

    def __init__(self):
        self._items: Dict[str, QueueItem] = {}
        self._counter: int = 0
        self._confirmed_links: Dict[str, Set[str]] = defaultdict(set) # entity -> linked entities
        self._decoupled_pairs: Set[Tuple[str, str]] = set()
        self._pair_index: Dict[Tuple[str, str], str] = {}

    def _pair_key(self, id_a: str, id_b: str) -> Tuple[str, str]:
        return (id_a, id_b) if id_a <= id_b else (id_b, id_a)

    def get_existing(self, id_a: str, id_b: str) -> Optional[QueueItem]:
        """Return existing queue item for candidate pair if already queued."""
        item_id = self._pair_index.get(self._pair_key(id_a, id_b))
        return self._items.get(item_id) if item_id else None

    def enqueue(self, match: MatchResult) -> QueueItem:
        """Add candidate pair to queue, avoiding duplicate entries on re-score."""
        pkey = self._pair_key(match.candidate_id_a, match.candidate_id_b)
        existing_id = self._pair_index.get(pkey)
        if existing_id and existing_id in self._items:
            return self._items[existing_id]

        self._counter += 1
        item_id = f"QITEM-{self._counter:05d}"
        now_str = datetime.now(timezone.utc).isoformat()
        
        item = QueueItem(
            item_id=item_id,
            match_result=match,
            enqueued_timestamp=now_str
        )
        self._items[item_id] = item
        self._pair_index[pkey] = item_id
        return item

    def get_pending_items(self) -> List[QueueItem]:
        """Fetch all items pending reviewer action."""
        return [item for item in self._items.values() if item.status == "PENDING"]

    def adjudicate(
        self,
        item_id: str,
        action: AdjudicationAction,
        reviewer_id: str,
        notes: str = ""
    ) -> QueueItem:
        """Record reviewer adjudication decision with audit trail."""
        if item_id not in self._items:
            raise KeyError(f"Queue item {item_id} not found")

        item = self._items[item_id]
        item.status = "ADJUDICATED"
        item.adjudicated_action = action
        item.adjudicated_by = reviewer_id
        item.adjudication_timestamp = datetime.now(timezone.utc).isoformat()
        item.reviewer_notes = notes

        a_id = item.match_result.candidate_id_a
        b_id = item.match_result.candidate_id_b
        pkey = self._pair_key(a_id, b_id)

        if action == AdjudicationAction.LINK:
            self._decoupled_pairs.discard(pkey)
            self._confirmed_links[a_id].add(b_id)
            self._confirmed_links[b_id].add(a_id)
        elif action == AdjudicationAction.DECOUPLE:
            self._decoupled_pairs.add(pkey)
            self._confirmed_links[a_id].discard(b_id)
            self._confirmed_links[b_id].discard(a_id)

        return item

    def is_confirmed_link(self, id_a: str, id_b: str) -> bool:
        """Check if two entities have been confirmed as linked by a human reviewer."""
        return id_b in self._confirmed_links.get(id_a, set())

    def is_decoupled(self, id_a: str, id_b: str) -> bool:
        """Check if a reviewer explicitly decoupled this pair."""
        return self._pair_key(id_a, id_b) in self._decoupled_pairs

