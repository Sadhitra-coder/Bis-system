"""app/acquisition/sync/__init__.py

Master Acquisition Synchronization and Pipeline Orchestration.
"""

from app.acquisition.sync.coordinator import AcquisitionCoordinator, default_coordinator

__all__ = ["AcquisitionCoordinator", "default_coordinator"]
