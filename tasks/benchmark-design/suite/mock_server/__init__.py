"""CANDIDATE-BUILT STAND-IN: CPU mock of an OpenAI-compatible streaming server.

The organizer's engine (llm-d on MI355X) is unavailable without a GPU. This package is a
deterministic, seeded stand-in used only to validate the runner and evaluator. It is not
a performance model of any real engine; its timings are injected, not measured.
"""

from mock_server.faults import PROFILES, FaultProfile, profile_by_name
from mock_server.server import MockServer, serve

__all__ = ["PROFILES", "FaultProfile", "MockServer", "profile_by_name", "serve"]
