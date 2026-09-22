"""app/acquisition/queue/priority_queue.py

Safe Acquisition Priority Queue (P0 to P5).

Levels:
- P0: Mandatory Quality Control Orders (QCOs) & Gazette Notifications
- P1: Product-Specific Certification Information / Product Manuals (PMs)
- P2: Standards Metadata (Know Your Standards)
- P3: Publicly / Authorized Downloadable Standards
- P4: Amendments, Revisions, and Corrigenda
- P5: Remaining Voluntary / General Standards
"""

import heapq
import logging
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.acquisition.models import DocumentDiscoveryRecord

logger = logging.getLogger(__name__)


class QueueItem(BaseModel):
    """Container for priority sorting."""
    priority: int
    created_at: float
    record: DocumentDiscoveryRecord

    def __lt__(self, other: "QueueItem") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at


class SafeAcquisitionQueue:
    """Multi-level priority queue with queue status reporting."""

    def __init__(self):
        self._heap: List[QueueItem] = []

    @staticmethod
    def calculate_priority(record: DocumentDiscoveryRecord) -> int:
        """Assign P0-P5 priority based on regulatory importance."""
        doc_type = record.document_type.upper()

        # P0: QCOs & Statutory Orders
        if any(k in doc_type for k in ["QCO", "GAZETTE", "STATUTORY"]):
            return 0

        # P1: Product Certification Guidelines / Manuals
        if any(k in doc_type for k in ["MANUAL", "PRODUCT_MANUAL", "STI", "TESTING"]):
            return 1

        # P2: Standards Metadata
        if "METADATA" in doc_type:
            return 2

        # P3: Authorized / Public Full Standards
        if record.rights_status == "PUBLIC" and "STANDARD" in doc_type:
            return 3

        # P4: Amendments & Revisions
        if any(k in doc_type for k in ["AMENDMENT", "REVISION", "CORRIGENDA"]):
            return 4

        # P5: Remaining
        return 5

    def push(self, record: DocumentDiscoveryRecord, explicit_priority: Optional[int] = None) -> None:
        prio = explicit_priority if explicit_priority is not None else self.calculate_priority(record)
        entry = QueueItem(
            priority=prio,
            created_at=record.discovered_at or time.time(),
            record=record
        )
        heapq.heappush(self._heap, entry)

    def pop(self) -> Optional[DocumentDiscoveryRecord]:
        if not self._heap:
            return None
        return heapq.heappop(self._heap).record

    def peek(self) -> Optional[DocumentDiscoveryRecord]:
        if not self._heap:
            return None
        return self._heap[0].record

    def is_empty(self) -> bool:
        return len(self._heap) == 0

    def __len__(self) -> int:
        return len(self._heap)

    def get_status_report(self) -> Dict[str, Any]:
        """Report queue size and distribution across priority tiers."""
        counts = {f"P{i}": 0 for i in range(6)}
        for item in self._heap:
            key = f"P{item.priority}"
            counts[key] = counts.get(key, 0) + 1
        return {
            "total_queued": len(self._heap),
            "distribution": counts,
        }


default_acquisition_queue = SafeAcquisitionQueue()
