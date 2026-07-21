from dataclasses import fields
from pathlib import Path
import re

from exhibit_a.models.case import Case, Evidence, Hypothesis, RunResult
from exhibit_a.models.case import TestArtifact as CaseTestArtifact


def test_typescript_case_schema_matches_python_dataclasses():
    case_ts = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "case.ts"
    source = case_ts.read_text()

    for name, model in {
        "TestArtifact": CaseTestArtifact,
        "RunResult": RunResult,
        "Evidence": Evidence,
        "Hypothesis": Hypothesis,
        "Case": Case,
    }.items():
        match = re.search(rf"export interface {name} \{{(?P<body>.*?)\n\}}", source, re.S)
        assert match, f"missing TypeScript interface {name}"
        typescript_fields = re.findall(r"^  ([a-z_]+):", match.group("body"), re.M)
        python_fields = [field.name for field in fields(model)]
        assert typescript_fields == python_fields
