from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from exhibit_a.signatures import (
    EEF_PAYLOAD_TYPE,
    PASSPORT_PAYLOAD_TYPE,
    canonical_json,
    public_key_from_seed,
    sign_envelope,
    verify_envelope,
)


FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "adr" / "fixtures"
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
EVALUATED_AT = datetime.fromisoformat("2026-09-02T00:00:00Z")
ROOT = (FIXTURES / "eef-v4-trust-root.json").read_bytes()
ANCHOR = (FIXTURES / "eef-v4-trust-anchor.json").read_bytes()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.mark.parametrize(
    ("envelope_name", "purpose", "payload_type", "profile"),
    [
        ("eef-v4-attestation.dsse.json", "eef", EEF_PAYLOAD_TYPE, "eef-ed25519-v1"),
        (
            "passport-v3.dsse.json",
            "passport",
            PASSPORT_PAYLOAD_TYPE,
            "passport-ed25519-v1",
        ),
    ],
)
def test_checked_vectors_verify_under_the_anchored_policy(
    envelope_name: str,
    purpose: str,
    payload_type: str,
    profile: str,
) -> None:
    payload, identity = verify_envelope(
        _fixture(envelope_name),
        trust_root=ROOT,
        trust_anchor=ANCHOR,
        purpose=purpose,
        evaluated_at=EVALUATED_AT,
    )

    assert identity.root_id == "https://exhibit-a.dev/trust-roots/reference"
    assert identity.root_version == 1
    assert identity.publisher_id == "https://exhibit-a.dev/publishers/reference"
    assert identity.verified_key_ids == (
        "sha256:06e3fd8fda29bb60ab59557de61edb0aecdb231134be30e75b455f8e1b792fa9",
    )
    assert (
        payload.get("signatureProfile") == profile
        or payload["predicate"]["signatureProfile"] == profile
    )

    recreated = sign_envelope(
        payload,
        payload_type=payload_type,
        private_key_seed=SEED,
        trust_root=ROOT,
        policy_id=identity.policy_id,
        purpose=purpose,
    )
    assert (
        canonical_json(recreated) + b"\n"
        == canonical_json(json.loads(_fixture(envelope_name))) + b"\n"
    )


def test_public_material_does_not_grant_signing_authority() -> None:
    public_key = public_key_from_seed(SEED)
    statement = json.loads(
        base64.b64decode(json.loads(_fixture("eef-v4-attestation.dsse.json"))["payload"])
    )

    with pytest.raises(ValueError, match="private key seed"):
        sign_envelope(
            statement,
            payload_type=EEF_PAYLOAD_TYPE,
            private_key_seed=public_key[:-1],
            trust_root=ROOT,
            policy_id="eef-reference-v1",
            purpose="eef",
        )
    with pytest.raises(ValueError, match="not authorized"):
        sign_envelope(
            statement,
            payload_type=EEF_PAYLOAD_TYPE,
            private_key_seed=public_key,
            trust_root=ROOT,
            policy_id="eef-reference-v1",
            purpose="eef",
        )


@pytest.mark.parametrize("mutation", ["payload", "signature", "root", "anchor", "purpose"])
def test_public_signature_tampering_fails_closed(mutation: str) -> None:
    envelope = json.loads(_fixture("eef-v4-attestation.dsse.json"))
    root = ROOT
    anchor = ANCHOR
    purpose = "eef"
    if mutation == "payload":
        payload = bytearray(base64.b64decode(envelope["payload"]))
        payload[-2] ^= 1
        envelope["payload"] = base64.b64encode(payload).decode()
    elif mutation == "signature":
        signature = bytearray(base64.b64decode(envelope["signatures"][0]["sig"]))
        signature[0] ^= 1
        envelope["signatures"][0]["sig"] = base64.b64encode(signature).decode()
    elif mutation == "root":
        changed = bytearray(root)
        changed[-2] = ord(" ")
        root = bytes(changed)
    elif mutation == "anchor":
        parsed = json.loads(anchor)
        parsed["minimumRootVersion"] = 2
        anchor = canonical_json(parsed)
    else:
        purpose = "passport"

    with pytest.raises(ValueError):
        verify_envelope(
            canonical_json(envelope),
            trust_root=root,
            trust_anchor=anchor,
            purpose=purpose,
            evaluated_at=EVALUATED_AT,
        )


def test_wrong_key_hint_cannot_route_signature_verification() -> None:
    envelope = json.loads(_fixture("eef-v4-attestation.dsse.json"))
    envelope["signatures"][0]["keyid"] = "sha256:" + "0" * 64

    payload, identity = verify_envelope(
        canonical_json(envelope),
        trust_root=ROOT,
        trust_anchor=ANCHOR,
        purpose="eef",
        evaluated_at=EVALUATED_AT,
    )

    assert payload["predicateType"] == "https://exhibit-a.dev/eef/v4"
    assert identity.verified_key_ids[0] != envelope["signatures"][0]["keyid"]


def test_duplicate_json_keys_and_expired_roots_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        verify_envelope(
            b'{"payload":"first","payload":"second"}',
            trust_root=ROOT,
            trust_anchor=ANCHOR,
            purpose="eef",
            evaluated_at=EVALUATED_AT,
        )

    with pytest.raises(ValueError, match="expired"):
        verify_envelope(
            _fixture("eef-v4-attestation.dsse.json"),
            trust_root=ROOT,
            trust_anchor=ANCHOR,
            purpose="eef",
            evaluated_at=datetime.fromisoformat("2030-01-01T00:00:00Z"),
        )


def test_revoked_key_cannot_satisfy_a_threshold() -> None:
    root = json.loads(ROOT)
    changed = copy.deepcopy(root)
    changed["keys"][0]["status"] = "revoked"
    changed_root = canonical_json(changed) + b"\n"
    anchor = json.loads(ANCHOR)
    anchor["pinnedRootSha256"] = hashlib.sha256(changed_root).hexdigest()

    with pytest.raises(ValueError, match="cannot meet"):
        verify_envelope(
            _fixture("eef-v4-attestation.dsse.json"),
            trust_root=changed_root,
            trust_anchor=canonical_json(anchor),
            purpose="eef",
            evaluated_at=EVALUATED_AT,
        )
