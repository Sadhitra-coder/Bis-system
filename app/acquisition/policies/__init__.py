"""app/acquisition/policies/__init__.py"""
from app.acquisition.policies.access_control import AccessControlPolicy, default_access_policy

__all__ = ["AccessControlPolicy", "default_access_policy"]
