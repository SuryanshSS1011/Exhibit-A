import re
from dataclasses import fields
from pathlib import Path

from exhibit_a.models.case import (
    Case,
    Evidence,
    EvidenceMinimization,
    EvidenceProvenance,
    EvidenceStrength,
    Hypothesis,
    ProposalRun,
    RunResult,
    StrengthComponent,
    TruthAssessment,
)
from exhibit_a.models.case import TestArtifact as CaseTestArtifact


def test_typescript_case_schema_matches_python_dataclasses():
    case_ts = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "case.ts"
    source = case_ts.read_text()

    for name, model in {
        "TestArtifact": CaseTestArtifact,
        "RunResult": RunResult,
        "Evidence": Evidence,
        "EvidenceProvenance": EvidenceProvenance,
        "TruthAssessment": TruthAssessment,
        "EvidenceMinimization": EvidenceMinimization,
        "StrengthComponent": StrengthComponent,
        "EvidenceStrength": EvidenceStrength,
        "Hypothesis": Hypothesis,
        "ProposalRun": ProposalRun,
        "Case": Case,
    }.items():
        match = re.search(rf"export interface {name} \{{(?P<body>.*?)\n\}}", source, re.S)
        assert match, f"missing TypeScript interface {name}"
        typescript_fields = re.findall(r"^  ([a-z_0-9]+)\??:", match.group("body"), re.M)
        python_fields = [field.name for field in fields(model)]
        assert typescript_fields == python_fields
