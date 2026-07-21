"""Exhibit A — an evidence engine for code that's only allowed to speak with proof.

Public surface:
    from exhibit_a import EvidenceEngine, EngineConfig
    from exhibit_a.hypothesis.generator import Claim, StubGenerator
    from exhibit_a.executor.local_exec import LocalExecutor
    from exhibit_a.executor.docker_exec import DockerExecutor
    from exhibit_a.models.case import Case, Verdict, Mode
"""

from .engine import EngineConfig, EvidenceEngine

__all__ = ["EvidenceEngine", "EngineConfig"]
__version__ = "0.0.1"
