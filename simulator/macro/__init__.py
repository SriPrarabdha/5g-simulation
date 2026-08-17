"""Deterministic 30-second cohort simulator."""

from .config import ScenarioConfig, load_scenario
from .controllers import controller_by_name
from .engine import RunOutcome, Simulator
from .sinks import (
    ArtifactDescriptor,
    AuditSink,
    BoundedMemorySink,
    CompositeSink,
    DecisionTraceSink,
    JsonlSink,
    ParquetSink,
    StepSink,
    SummarySink,
)

__all__ = [
    "ArtifactDescriptor", "AuditSink", "BoundedMemorySink", "CompositeSink",
    "DecisionTraceSink", "JsonlSink", "ParquetSink", "RunOutcome", "ScenarioConfig", "Simulator",
    "StepSink", "SummarySink", "controller_by_name", "load_scenario",
]
