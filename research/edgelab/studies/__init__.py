"""Pre-registered event studies (RESEARCH_PROTOCOL.md sections 3-4).

``context`` builds the causal feature set once per instrument and period, ``detectors``
and ``controls`` produce events and control groups, ``inference`` holds the date-cluster
bootstrap tests, ``catalog`` defines one function per study ID (parameters in
``configs/event_studies.yaml``) and ``runner`` applies the gates and writes the outputs.
"""

from .catalog import HORIZONS, STUDIES, StudyData
from .context import StudyContext
from .inference import TestInput, evaluate

__all__ = ["HORIZONS", "STUDIES", "StudyContext", "StudyData", "TestInput", "evaluate"]
