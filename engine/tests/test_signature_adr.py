from __future__ import annotations

import base64
import binascii
import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


FIXTURES = Path(__file__).resolve().parents[2] / "docs" / "adr" / "fixtures"
ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
ED25519_PKCS8_SEED_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
PUBLISHER_ID = "https://exhibit-a.dev/publishers/reference"
ROOT_ID = "https://exhibit-a.dev/trust-roots/reference"
KEY_ID = "sha256:06e3fd8fda29bb60ab59557de61edb0aecdb231134be30e75b455f8e1b792fa9"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        assert key not in result, f"fixture contains duplicate JSON key: {key}"
        result[key] = value
    return result


def _parse(content: bytes) -> dict[str, object]:
    value = json.loads(content, object_pairs_hook=_unique_object)
    assert isinstance(value, dict)
    return value


def _load(name: str) -> dict[str, object]:
    return _parse((FIXTURES / name).read_bytes())


def _strict_base64(value: object) -> bytes:
    assert isinstance(value, str)
    try:
        decoded = base64.b64decode(value, validate=True)
    except binascii.Error as error:  # pragma: no cover - assertion provides useful context
        raise AssertionError("fixture contains invalid standard Base64") from error
    assert base64.b64encode(decoded).decode("ascii") == value
    return decoded


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _pae(payload_type: bytes, payload: bytes) -> bytes:
    return b" ".join(
        (
            b"DSSEv1",
            str(len(payload_type)).encode("ascii"),
            payload_type,
            str(len(payload)).encode("ascii"),
            payload,
        )
    )


def _one_signature(envelope: dict[str, object]) -> dict[str, object]:
    assert set(envelope) == {"payload", "payloadType", "signatures"}
    signatures = envelope["signatures"]
    assert isinstance(signatures, list) and len(signatures) == 1
    signature = signatures[0]
    assert isinstance(signature, dict)
    assert set(signature) == {"keyid", "sig"}
    return signature


def test_eef_v4_examples_have_the_decided_schema_and_binding() -> None:
    envelope = _load("eef-v4-attestation.dsse.json")
    root_path = FIXTURES / "eef-v4-trust-root.json"
    root = _parse(root_path.read_bytes())
    anchor = _load("eef-v4-trust-anchor.json")
    vector = _load("eef-v4-signature-vector.json")
    signature = _one_signature(envelope)

    assert envelope["payloadType"] == "application/vnd.in-toto+json"
    payload = _strict_base64(envelope["payload"])
    statement = _parse(payload)
    assert payload == _canonical(statement)

    manifest_bytes = (FIXTURES / "eef-v4-manifest.json").read_bytes()
    manifest = _parse(manifest_bytes)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    assert manifest == {
        "claimType": "bug_flip",
        "entries": {
            "case.json": {
                "sha256": hashlib.sha256(b"").hexdigest(),
                "size": 0,
            }
        },
        "format": "eef/v4",
    }
    assert statement == {
        "_type": "https://in-toto.io/Statement/v1",
        "predicate": {
            "claimType": "bug_flip",
            "format": "eef/v4",
            "publisher": {"id": PUBLISHER_ID},
            "signatureProfile": "eef-ed25519-v1",
            "verdict": "VERIFIED",
        },
        "predicateType": "https://exhibit-a.dev/eef/v4",
        "subject": [
            {
                "digest": {"sha256": manifest_sha256},
                "name": "manifest.json",
            }
        ],
    }
    assert manifest_sha256 == vector["manifestSha256"]

    assert set(root) == {
        "expiresAt",
        "keys",
        "policies",
        "rootId",
        "rootVersion",
        "schemaVersion",
    }
    assert root["schemaVersion"] == "exhibit-a-trust-root/v1"
    assert root["rootId"] == ROOT_ID
    assert root["rootVersion"] == 1
    assert set(anchor) == {
        "expectedPolicyIds",
        "minimumRootVersion",
        "pinnedRootSha256",
        "provisionedAt",
        "rootId",
        "schemaVersion",
        "testOnly",
    }
    assert anchor["schemaVersion"] == "exhibit-a-trust-anchor/v1"
    assert anchor["rootId"] == root["rootId"]
    assert anchor["minimumRootVersion"] == root["rootVersion"]
    assert anchor["expectedPolicyIds"] == {
        "eef": "eef-reference-v1",
        "passport": "passport-reference-v1",
    }
    assert anchor["pinnedRootSha256"] == hashlib.sha256(root_path.read_bytes()).hexdigest()
    assert anchor["testOnly"] is True

    evaluated_at = datetime.fromisoformat(str(vector["evaluationTime"]))
    expires_at = datetime.fromisoformat(str(root["expiresAt"]))
    provisioned_at = datetime.fromisoformat(str(anchor["provisionedAt"]))
    assert provisioned_at <= evaluated_at < expires_at

    keys = root["keys"]
    assert isinstance(keys, list) and len(keys) == 1
    key = keys[0]
    assert isinstance(key, dict)
    assert set(key) == {"algorithm", "keyId", "label", "publicKey", "status"}
    assert key["algorithm"] == "ed25519"
    assert key["status"] == "active"
    public_key_record = key["publicKey"]
    assert isinstance(public_key_record, dict)
    assert set(public_key_record) == {"encoding", "value"}
    assert public_key_record["encoding"] == "raw-base64"
    public_key = _strict_base64(public_key_record["value"])
    assert len(public_key) == 32
    expected_key_id = "sha256:" + hashlib.sha256(ED25519_SPKI_PREFIX + public_key).hexdigest()
    assert expected_key_id == KEY_ID
    assert key["keyId"] == expected_key_id == signature["keyid"] == vector["keyId"]

    policies = root["policies"]
    assert isinstance(policies, list) and len(policies) == 2
    policy_ids: set[object] = set()
    selectors: set[tuple[object, ...]] = set()
    for policy in policies:
        assert isinstance(policy, dict)
        assert set(policy) == {
            "authorizedKeyIds",
            "payloadType",
            "policyId",
            "predicateType",
            "profile",
            "publisherId",
            "purpose",
            "threshold",
        }
        policy_id = policy["policyId"]
        assert policy_id not in policy_ids
        policy_ids.add(policy_id)
        selector = tuple(
            policy[name]
            for name in (
                "profile",
                "purpose",
                "payloadType",
                "predicateType",
                "publisherId",
            )
        )
        assert selector not in selectors
        selectors.add(selector)
        authorized = policy["authorizedKeyIds"]
        assert authorized == [expected_key_id]
        assert policy["threshold"] == 1 <= len(set(authorized))

    expected_policy_ids = anchor["expectedPolicyIds"]
    assert isinstance(expected_policy_ids, dict)
    eef_policy = next(policy for policy in policies if policy["purpose"] == "eef")
    assert eef_policy["policyId"] == expected_policy_ids["eef"]
    predicate = statement["predicate"]
    assert isinstance(predicate, dict)
    publisher = predicate["publisher"]
    assert isinstance(publisher, dict)
    assert eef_policy == {
        "authorizedKeyIds": [expected_key_id],
        "payloadType": envelope["payloadType"],
        "policyId": "eef-reference-v1",
        "predicateType": statement["predicateType"],
        "profile": predicate["signatureProfile"],
        "publisherId": publisher["id"],
        "purpose": "eef",
        "threshold": 1,
    }

    assert vector["schemaVersion"] == "eef-signature-test-vector/v1"
    assert vector["testOnly"] is True
    assert vector["payloadType"] == envelope["payloadType"]
    assert vector["payloadBase64"] == envelope["payload"]
    assert vector["expectedSignatureBase64"] == signature["sig"]
    assert vector["publicKeyBase64"] == public_key_record["value"]
    assert hashlib.sha256(payload).hexdigest() == vector["payloadSha256"]


def test_passport_v3_example_is_bound_to_its_own_profile() -> None:
    envelope = _load("passport-v3.dsse.json")
    vector = _load("passport-v3-signature-vector.json")
    root = _load("eef-v4-trust-root.json")
    anchor = _load("eef-v4-trust-anchor.json")
    signature = _one_signature(envelope)

    assert envelope["payloadType"] == "application/vnd.exhibit-a.passport.v3+json"
    payload = _strict_base64(envelope["payload"])
    passport = _parse(payload)
    assert payload == _canonical(passport)
    assert passport == {
        "passportIssuer": {"id": PUBLISHER_ID},
        "schemaVersion": "exhibit-a-passport/v3",
        "signatureProfile": "passport-ed25519-v1",
        "sourceEef": {
            "format": "eef/v4",
            "manifestSha256": hashlib.sha256(
                (FIXTURES / "eef-v4-manifest.json").read_bytes()
            ).hexdigest(),
            "publisher": {"id": PUBLISHER_ID},
            "trustRoot": {
                "rootId": ROOT_ID,
                "rootSha256": anchor["pinnedRootSha256"],
                "rootVersion": root["rootVersion"],
            },
            "verifiedKeyIds": [KEY_ID],
        },
    }

    policies = root["policies"]
    assert isinstance(policies, list)
    expected_policy_ids = anchor["expectedPolicyIds"]
    assert isinstance(expected_policy_ids, dict)
    passport_policy = next(policy for policy in policies if policy["purpose"] == "passport")
    assert passport_policy["policyId"] == expected_policy_ids["passport"]
    passport_issuer = passport["passportIssuer"]
    assert isinstance(passport_issuer, dict)
    assert passport_policy == {
        "authorizedKeyIds": [KEY_ID],
        "payloadType": envelope["payloadType"],
        "policyId": "passport-reference-v1",
        "predicateType": None,
        "profile": passport["signatureProfile"],
        "publisherId": passport_issuer["id"],
        "purpose": "passport",
        "threshold": 1,
    }
    assert signature["keyid"] == vector["keyId"] == KEY_ID
    assert vector["schemaVersion"] == "passport-signature-test-vector/v1"
    assert vector["testOnly"] is True
    assert vector["payloadType"] == envelope["payloadType"]
    assert vector["payloadBase64"] == envelope["payload"]
    assert vector["expectedSignatureBase64"] == signature["sig"]
    keys = root["keys"]
    assert isinstance(keys, list) and len(keys) == 1
    root_key = keys[0]
    assert isinstance(root_key, dict)
    public_key_record = root_key["publicKey"]
    assert isinstance(public_key_record, dict)
    assert vector["publicKeyBase64"] == public_key_record["value"]
    assert hashlib.sha256(payload).hexdigest() == vector["payloadSha256"]


def test_signature_vectors_are_independently_checked_by_openssl(tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    assert openssl is not None, "OpenSSL is required to check the ADR signature vectors"

    for name in ("eef-v4-signature-vector.json", "passport-v3-signature-vector.json"):
        vector = _load(name)
        payload_type = str(vector["payloadType"]).encode("ascii")
        payload = _strict_base64(vector["payloadBase64"])
        message = _pae(payload_type, payload)
        expected_signature = _strict_base64(vector["expectedSignatureBase64"])
        public_key = _strict_base64(vector["publicKeyBase64"])
        seed = bytes.fromhex(str(vector["privateKeySeedHex"]))

        assert len(seed) == len(public_key) == 32
        assert len(expected_signature) == 64
        assert hashlib.sha256(message).hexdigest() == vector["paeSha256"]

        vector_path = tmp_path / name
        vector_path.mkdir()
        message_path = vector_path / "pae.bin"
        private_key_path = vector_path / "private.der"
        public_key_path = vector_path / "public.der"
        generated_signature_path = vector_path / "generated.sig"
        expected_signature_path = vector_path / "expected.sig"
        message_path.write_bytes(message)
        private_key_path.write_bytes(ED25519_PKCS8_SEED_PREFIX + seed)
        public_key_path.write_bytes(ED25519_SPKI_PREFIX + public_key)
        expected_signature_path.write_bytes(expected_signature)

        subprocess.run(
            [
                openssl,
                "pkeyutl",
                "-sign",
                "-inkey",
                str(private_key_path),
                "-keyform",
                "DER",
                "-rawin",
                "-in",
                str(message_path),
                "-out",
                str(generated_signature_path),
            ],
            check=True,
            capture_output=True,
        )
        assert generated_signature_path.read_bytes() == expected_signature

        subprocess.run(
            [
                openssl,
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(public_key_path),
                "-keyform",
                "DER",
                "-rawin",
                "-in",
                str(message_path),
                "-sigfile",
                str(expected_signature_path),
            ],
            check=True,
            capture_output=True,
        )
