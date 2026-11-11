"""M4 Application Anomaly and Template Detection Package."""

from .application import ApplicationVelocityTracker
from .template import MinHashTemplateDetector

__all__ = ["ApplicationVelocityTracker", "MinHashTemplateDetector"]
