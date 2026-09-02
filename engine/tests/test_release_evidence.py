from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from exhibit_a.connectors import (
    CICheckRun,
    CIStatus,
    ConnectorOutput,
    ConnectorSecurity,
    EvidenceKind,
    EvidenceProvenance,
    Freshness,
)
from exhibit_a.connectors.base import hash_payload
from exhibit_a.release_evidence import (
    RELEASE_EVIDENCE_SCHEMA,
    create_release_record,
    parse_policy_document,
    public_release_projection,
    validate_release_evidence,
)

REVISION = "1f9473f8d6940935ec45a41cb518d9038e0bea0e"
OBSERVED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
EVALUATED = OBSERVED + timedelta(minutes=5)
PRIVATE_SOURCE = "https://private-api.example.test/repos/example/project"
PRIVATE_DESCRIPTION = "raw-body-marker-TOP-SECRET"


def _check(name: str, conclusion: str = "success") -> CICheckRun:
    return CICheckRun(
        name=name,
        status="completed",
        conclusion=conclusion,
        started_at=(OBSERVED - timedelta(minutes=3)).isoformat(),
        completed_at=(OBSERVED - timedelta(minutes=1)).isoformat(),
    )


def _output(*checks: CICheckRun) -> ConnectorOutput[CIStatus]:
    status = CIStatus(
        repository="example/project",
        revision=REVISION,
        reported_total=len(checks),
        checks=checks,
    )
    request = {
        "repository": status.repository,
        "revision": status.revision,
        "source": PRIVATE_SOURCE,
    }
    request_sha256 = hash_payload(request)
    response_sha256 = hash_payload(status.payload())
    provenance = EvidenceProvenance(
        evidence_id="a" * 32,
        connector_id="github_ci_status",
        connector_version="1",
        capability=EvidenceKind.CI_STATUS,
        source=PRIVATE_SOURCE,
        source_revision=REVISION,
        observed_at=OBSERVED.isoformat(),
        source_updated_at=(OBSERVED - timedelta(minutes=1)).isoformat(),
        freshness=Freshness.POINT_IN_TIME,
        description=PRIVATE_DESCRIPTION,
        request_sha256=request_sha256,
        response_sha256=response_sha256,
        artifact_sha256="b" * 64,
        content_sha256=hash_payload(
            {"request_sha256": request_sha256, "response_sha256": response_sha256}
        ),
        security=ConnectorSecurity(
            source_access="read_only",
            network_access="host_unrestricted",
            isolation="in_process",
            credential_access="ambient_host",
        ),
    )
    return ConnectorOutput(status, provenance)


def _policy(**overrides: object) -> dict:
    value = {
        "schema_version": "release-policy/v1",
        "name": "required-ci",
        "required_checks": ["engine", "web"],
        "max_age_s": 3600,
    }
    value.update(overrides)
    return value


def _receipt(output: ConnectorOutput[CIStatus]) -> dict:
    return {
        "payload_schema": "ci-status/v1",
        "request": {
            "repository": output.payload.repository,
            "revision": output.payload.revision,
            "source": output.provenance.source,
        },
        "payload": {
            "repository": output.payload.repository,
            "revision": output.payload.revision,
            "reported_total": output.payload.reported_total,
            "checks": [asdict(check) for check in output.payload.checks],
        },
        "provenance": asdict(output.provenance),
    }


def _case(record: dict, release: str, reason: str) -> dict:
    return {
        "truth": {"release": release, "release_reason": reason},
        "release_evidence": record,
    }


def test_safe_record_is_result_free_and_rederived_offline():
    output = _output(_check("engine"), _check("web"))
    named = parse_policy_document(_policy())

    record, assessment = create_release_record(output, named, evaluated_at=EVALUATED)

    assert record == {
        "schema_version": RELEASE_EVIDENCE_SCHEMA,
        "policy": _policy(),
        "receipt_evidence_id": "a" * 32,
        "evaluated_at": EVALUATED.isoformat(),
    }
    assert "release" not in record
    validated = validate_release_evidence(
        _case(record, assessment.release.value, assessment.reason),
        [_receipt(output)],
    )
    assert validated is not None
    assert validated.assessment == assessment
    assert validated.output == output


def test_unsafe_truth_is_rederived_without_changing_any_other_truth():
    output = _output(_check("engine", "failure"), _check("web"))
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    case = {
        "verdict": "FAILED",
        "truth": {
            "execution": "FAILED",
            "goal": "UNCERTAIN",
            "release": assessment.release.value,
            "release_reason": assessment.reason,
        },
        "release_evidence": record,
    }

    validated = validate_release_evidence(case, [_receipt(output)])

    assert validated is not None
    assert validated.assessment.release.value == "UNSAFE"
    assert case["verdict"] == "FAILED"
    assert case["truth"]["execution"] == "FAILED"
    assert case["truth"]["goal"] == "UNCERTAIN"


def test_missing_record_is_valid_only_for_not_assessed_truth():
    assert validate_release_evidence({"truth": {"release": "NOT_ASSESSED"}}, []) is None

    with pytest.raises(ValueError, match="requires a release evidence record"):
        validate_release_evidence({"truth": {"release": "SAFE"}}, [])


@pytest.mark.parametrize(
    "document",
    [
        {},
        {**_policy(), "extra": True},
        _policy(schema_version="release-policy/v2"),
        _policy(name="../unsafe"),
        _policy(name="x" * 65),
        _policy(required_checks=[]),
        _policy(required_checks=("engine",)),
        _policy(required_checks=["engine", "engine"]),
        _policy(max_age_s=0),
    ],
)
def test_policy_document_schema_is_strict(document: dict):
    with pytest.raises(ValueError):
        parse_policy_document(document)


def test_record_truth_policy_and_receipt_tampering_fail_closed():
    output = _output(_check("engine"), _check("web"))
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    case = _case(record, assessment.release.value, assessment.reason)
    receipt = _receipt(output)

    false_truth = copy.deepcopy(case)
    false_truth["truth"]["release"] = "UNSAFE"
    with pytest.raises(ValueError, match="does not match"):
        validate_release_evidence(false_truth, [receipt])

    wrong_receipt = copy.deepcopy(case)
    wrong_receipt["release_evidence"]["receipt_evidence_id"] = "c" * 32
    with pytest.raises(ValueError, match="different connector receipt"):
        validate_release_evidence(wrong_receipt, [receipt])

    changed_policy = copy.deepcopy(case)
    changed_policy["release_evidence"]["policy"]["required_checks"] = ["missing"]
    with pytest.raises(ValueError, match="does not match"):
        validate_release_evidence(changed_policy, [receipt])

    changed_payload = copy.deepcopy(receipt)
    changed_payload["payload"]["checks"][0]["conclusion"] = "failure"
    with pytest.raises(ValueError, match="response digest"):
        validate_release_evidence(case, [changed_payload])


def test_release_record_and_receipt_shapes_are_exact():
    output = _output(_check("engine"), _check("web"))
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    case = _case(record, assessment.release.value, assessment.reason)
    receipt = _receipt(output)

    extra_record = copy.deepcopy(case)
    extra_record["release_evidence"]["result"] = "SAFE"
    with pytest.raises(ValueError, match="record has an invalid shape"):
        validate_release_evidence(extra_record, [receipt])
    with pytest.raises(ValueError, match="exactly one"):
        validate_release_evidence(case, [])
    with pytest.raises(ValueError, match="exactly one"):
        validate_release_evidence(case, [receipt, receipt])

    malformed = copy.deepcopy(receipt)
    malformed["provenance"]["security"]["source_access"] = "ambient_write"
    with pytest.raises(ValueError, match="security"):
        validate_release_evidence(case, [malformed])


def test_public_projection_is_allowlisted_and_filters_optional_checks():
    output = _output(
        _check("engine"),
        _check("optional-private-job"),
        _check("web"),
    )
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    validated = validate_release_evidence(
        _case(record, assessment.release.value, assessment.reason),
        [_receipt(output)],
    )
    assert validated is not None

    projection = public_release_projection(validated)
    encoded = repr(projection)

    assert [check["name"] for check in projection["checks"]] == ["engine", "web"]
    assert projection["release"] == "SAFE"
    assert set(projection["provenance"]) == {
        "request_sha256",
        "response_sha256",
        "artifact_sha256",
        "content_sha256",
    }
    assert set(projection["policy"]) == {
        "schema_version",
        "name",
        "required_checks",
        "max_age_s",
        "sha256",
    }
    assert projection["collection"] == {
        "reported_total": 3,
        "collected_count": 3,
        "omitted_check_count": 1,
        "all_check_names_unique": True,
    }
    assert "optional-private-job" not in encoded
    assert "example/project" not in encoded
    assert PRIVATE_SOURCE not in encoded
    assert PRIVATE_DESCRIPTION not in encoded
    assert "security" not in encoded
    assert "raw_body" not in encoded


def test_noncanonical_time_and_request_digest_tampering_are_rejected():
    output = _output(_check("engine"), _check("web"))
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    case = _case(record, assessment.release.value, assessment.reason)
    receipt = _receipt(output)

    noncanonical = copy.deepcopy(case)
    noncanonical["release_evidence"]["evaluated_at"] = "2026-09-01T12:05:00Z"
    with pytest.raises(ValueError, match="canonical UTC"):
        validate_release_evidence(noncanonical, [receipt])

    bad_request = copy.deepcopy(receipt)
    bad_request["request"]["repository"] = "other/project"
    with pytest.raises(ValueError, match="repository"):
        validate_release_evidence(case, [bad_request])


def test_public_uncertain_reason_does_not_disclose_optional_duplicate_name():
    output = _output(
        _check("engine"),
        _check("private-optional-job"),
        _check("private-optional-job"),
        _check("web"),
    )
    record, assessment = create_release_record(
        output, parse_policy_document(_policy()), evaluated_at=EVALUATED
    )
    validated = validate_release_evidence(
        _case(record, assessment.release.value, assessment.reason),
        [_receipt(output)],
    )
    assert validated is not None

    projection = public_release_projection(validated)

    assert projection["release"] == "UNCERTAIN"
    assert projection["reason"] == "CI status contains duplicate check names"
    assert "private-optional-job" not in repr(projection)
