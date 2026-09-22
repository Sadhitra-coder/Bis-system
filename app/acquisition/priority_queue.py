"""app/acquisition/priority_queue.py

Priority Queue for Controlled Regulatory Knowledge Acquisition.

Priority Levels:
- Priority 1: Mandatory Quality Control Orders (QCOs) & Gazette Notifications
- Priority 2: Product Certification Manuals & Schemes of Inspection and Testing (STI)
- Priority 3: High-demand Industrial Standards (IS 16444, IS 1293, IS 15885, IS 1786)
- Priority 4: General Voluntary Standards
"""

import heapq
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.acquisition.models import (
    AcquisitionPriority,
    DiscoveredItem,
    DocumentClass,
)

logger = logging.getLogger(__name__)


class PrioritizedAcquisitionItem(BaseModel):
    """Container for priority queue sorting."""
    priority: int
    created_at: float
    item: DiscoveredItem

    def __lt__(self, other: "PrioritizedAcquisitionItem") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.created_at < other.created_at


class AcquisitionPriorityQueue:
    """
    Multi-tier priority queue for scheduling document acquisition and ingestion.
    Prevents indiscriminate crawling and guarantees mandatory regulations are ingested first.
    """

    def __init__(self):
        self._heap: List[PrioritizedAcquisitionItem] = []

    def push(self, item: DiscoveredItem) -> None:
        """Add an item to the priority queue."""
        priority_val = item.priority if item.priority is not None else self.determine_priority(item)
        entry = PrioritizedAcquisitionItem(
            priority=priority_val.value if hasattr(priority_val, "value") else int(priority_val),
            created_at=getattr(item, "created_at", 0.0) or 0.0,
            item=item
        )
        heapq.heappush(self._heap, entry)

    def pop(self) -> Optional[DiscoveredItem]:
        """Extract the highest priority item."""
        if not self._heap:
            return None
        return heapq.heappop(self._heap).item

    def peek(self) -> Optional[DiscoveredItem]:
        """Inspect the highest priority item without removing it."""
        if not self._heap:
            return None
        return self._heap[0].item

    def is_empty(self) -> bool:
        """Check if the priority queue is empty."""
        return len(self._heap) == 0

    def __len__(self) -> int:
        return len(self._heap)

    @staticmethod
    def determine_priority(item: DiscoveredItem) -> AcquisitionPriority:
        """Assign priority based on regulatory class and legal standing."""
        doc_class = item.document_class

        # Priority 1: Mandatory QCOs and Gazette Statutory Notifications
        if doc_class in (DocumentClass.QCO, DocumentClass.QCO_AMENDMENT, DocumentClass.GAZETTE_NOTIFICATION):
            return AcquisitionPriority.PRIORITY_1_MANDATORY_QCO

        # Priority 2: Product Certification Manuals, STI, and Lab Directories
        if doc_class in (DocumentClass.PRODUCT_MANUAL, DocumentClass.TESTING_SCHEME, DocumentClass.LABORATORY_DIRECTORY):
            return AcquisitionPriority.PRIORITY_2_MANUALS_AND_SCHEMES

        # Priority 3: High-demand key industrial standards
        high_demand_patterns = ["IS 16444", "IS 1293", "IS 15885", "IS 1786", "IS 3055", "IS 1417", "IS 9873"]
        if any(pat in item.identifier for pat in high_demand_patterns):
            return AcquisitionPriority.PRIORITY_3_HIGH_DEMAND_STANDARDS

        # Priority 4: Remaining voluntary or general standards
        return AcquisitionPriority.PRIORITY_4_GENERAL_STANDARDS


default_priority_queue = AcquisitionPriorityQueue()
