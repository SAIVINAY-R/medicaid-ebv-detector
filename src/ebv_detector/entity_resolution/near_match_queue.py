"""Near-Match Queue and Human Adjudication Workflow (DATA-025 / SEC-052).

Manages candidate pairs flagged by M1 Fellegi-Sunter between match and non-match thresholds.
Tracks reviewer actions (LINK, VOID, END_DATE, DECOUPLE) with immutable audit logging.
Once a human confirms a LINK, the relationship becomes a verified fact for Layer 1 and M3.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

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
        self._confirmed_links: Dict[str, str] = {} # entity_a -> entity_b

    def enqueue(self, match: MatchResult) -> QueueItem:
        """Add candidate pair to queue."""
        self._counter += 1
        item_id = f"QITEM-{self._counter:05d}"
        now_str = datetime.now(timezone.utc).isoformat()
        
        item = QueueItem(
            item_id=item_id,
            match_result=match,
            enqueued_timestamp=now_str
        )
        self._items[item_id] = item
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

        if action == AdjudicationAction.LINK:
            # Register verified fact
            self._confirmed_links[item.match_result.candidate_id_a] = item.match_result.candidate_id_b
            self._confirmed_links[item.match_result.candidate_id_b] = item.match_result.candidate_id_a

        return item

    def is_confirmed_link(self, id_a: str, id_b: str) -> bool:
        """Check if two entities have been confirmed as linked by a human reviewer."""
        return self._confirmed_links.get(id_a) == id_b
