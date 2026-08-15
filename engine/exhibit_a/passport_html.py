"""Self-contained HTML rendering for verified public evidence passports."""

from __future__ import annotations

import html
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .passport import PASSPORT_SCHEMA, verify_passport

_MAX_JSON_BYTES = 1024 * 1024
_MAX_HTML_BYTES = 2 * 1024 * 1024
_SHA256_LENGTH = 64
_VERDICTS = {"VERIFIED", "PARTIAL", "FAILED", "UNCERTAIN"}
_EXECUTION = {"NOT_RUN", "COMPLETED", "FAILED"}


def create_html_passport(
    passport: str | Path,
    output: str | Path,
    *,
    signing_key: bytes,
) -> Path:
    """Verify a JSON passport and atomically render its standalone HTML view."""
    source = Path(passport).expanduser().absolute()
    destination = Path(output).expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    try:
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError("JSON passport must be a regular file")
        if destination == source or (
            destination.exists() and os.path.samestat(destination.stat(), source_stat)
        ):
            raise ValueError("HTML passport output must not overwrite its JSON passport")
        encoded = _read_bounded(descriptor, _MAX_JSON_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(encoded) > _MAX_JSON_BYTES:
        raise ValueError("JSON passport exceeds the 1 MiB size limit")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except RecursionError as exc:
        raise ValueError("JSON passport exceeds the nesting limit") from exc
    _validate_depth(payload)
    if not isinstance(payload, dict):
        raise TypeError("JSON passport must contain an object")
    if not verify_passport(payload, signing_key=signing_key):
        raise ValueError("JSON passport signature verification failed")
    _validate_render_schema(payload)
    rendered = render_html_passport(payload)
    if len(rendered.encode()) > _MAX_HTML_BYTES:
        raise ValueError("HTML passport exceeds the 2 MiB size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(destination, rendered)
    return destination


def _read_bounded(descriptor: int, limit: int) -> bytes:
    chunks = []
    remaining = limit
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _validate_depth(value: object, *, maximum: int = 64) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > maximum:
            raise ValueError("JSON passport exceeds the nesting limit")
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _validate_render_schema(passport: dict[str, Any]) -> None:
    verification = passport["verification"]
    privacy = passport["privacy"]
    if set(verification) != {
        "eef_format",
        "integrity_verified",
        "publisher_signature_verified",
        "execution_replayed",
        "manifest_sha256",
        "signature",
    }:
        raise ValueError("passport verification section is invalid")
    if (
        verification.get("integrity_verified") is not True
        or verification.get("publisher_signature_verified") is not True
        or verification.get("eef_format") not in {"eef/v1", "eef/v2"}
        or not (
            verification.get("execution_replayed") is None
            or isinstance(verification.get("execution_replayed"), bool)
        )
        or not _sha256(verification.get("manifest_sha256"))
    ):
        raise ValueError("passport verification claims are invalid")
    eef_signature = verification.get("signature")
    if (
        not isinstance(eef_signature, dict)
        or set(eef_signature) != {"algorithm", "value", "meaning"}
        or eef_signature.get("algorithm") != "hmac-sha256"
        or not _sha256(eef_signature.get("value"))
        or not isinstance(eef_signature.get("meaning"), str)
    ):
        raise ValueError("passport EEF signature metadata is invalid")
    if (
        set(privacy) != {"credential_free", "omits"}
        or privacy.get("credential_free") is not True
        or not isinstance(privacy.get("omits"), list)
        or not all(isinstance(item, str) for item in privacy["omits"])
    ):
        raise ValueError("passport privacy section is invalid")

    subject = passport["subject"]
    if passport["claim_type"] == "bug_flip":
        _validate_bug_subject(subject)
    else:
        _validate_refactor_subject(subject)


def _validate_bug_subject(subject: dict[str, Any]) -> None:
    expected = {
        "case_id_sha256",
        "verdict",
        "truth",
        "deterministic",
        "reruns",
        "test_sha256",
        "proposal_runs",
        "proposal_runs_omitted",
        "evidence_sources",
        "evidence_sources_omitted",
        "revisions",
    }
    if set(subject) != expected or subject.get("verdict") not in {"VERIFIED", "PARTIAL"}:
        raise ValueError("bug passport subject is invalid")
    if (
        not _sha256(subject.get("case_id_sha256"))
        or not _sha256(subject.get("test_sha256"))
        or subject.get("deterministic") is not True
        or not _bounded_int(subject.get("reruns"), maximum=20, minimum=1)
        or not _bounded_int(subject.get("proposal_runs_omitted"))
        or not _bounded_int(subject.get("evidence_sources_omitted"))
    ):
        raise ValueError("bug passport evidence summary is invalid")
    _validate_truth(
        subject.get("truth"),
        expected_goal=subject["verdict"],
        expected_execution="COMPLETED",
        expected_release="NOT_ASSESSED",
    )
    proposals = subject.get("proposal_runs")
    sources = subject.get("evidence_sources")
    revisions = subject.get("revisions")
    if not isinstance(proposals, list) or len(proposals) > 100:
        raise ValueError("bug passport proposal records are invalid")
    if not all(_valid_proposal(item) for item in proposals):
        raise ValueError("bug passport proposal records are invalid")
    if not isinstance(sources, list) or len(sources) > 1000:
        raise ValueError("bug passport evidence sources are invalid")
    if not all(_valid_evidence_source(item) for item in sources):
        raise ValueError("bug passport evidence sources are invalid")
    if not isinstance(revisions, dict) or not all(
        name in {"base_commit", "target_commit", "culprit_commit", "culprit_parent_commit"}
        and isinstance(value, str)
        and 7 <= len(value) <= 64
        and all(character in "0123456789abcdef" for character in value)
        for name, value in revisions.items()
    ):
        raise ValueError("bug passport revisions are invalid")


def _validate_refactor_subject(subject: dict[str, Any]) -> None:
    expected = {
        "evidence_schema",
        "verdict",
        "truth",
        "deterministic",
        "reruns_per_state",
        "contract_sha256",
        "states",
        "evidence_sources",
        "evidence_sources_omitted",
    }
    if set(subject) != expected or subject.get("verdict") not in _VERDICTS:
        raise ValueError("refactor passport subject is invalid")
    if (
        not isinstance(subject.get("evidence_schema"), str)
        or not isinstance(subject.get("deterministic"), bool)
        or not _bounded_int(subject.get("reruns_per_state"), minimum=2, maximum=20)
        or not _bounded_int(subject.get("evidence_sources_omitted"))
        or not _sha256(subject.get("contract_sha256"))
    ):
        raise ValueError("refactor passport evidence summary is invalid")
    _validate_truth(
        subject.get("truth"),
        expected_goal=subject["verdict"],
        expected_execution=None if subject["verdict"] == "UNCERTAIN" else "COMPLETED",
        expected_release="NOT_ASSESSED",
    )
    states = subject.get("states")
    if not isinstance(states, dict) or set(states) != {"base", "target"}:
        raise ValueError("refactor passport states are invalid")
    for state in states.values():
        if (
            not isinstance(state, dict)
            or set(state) != {"status", "runs", "exit_codes"}
            or state.get("status") not in {"NOT_RUN", "PASS", "FAIL", "FLAKY", "INFRA"}
            or not _bounded_int(state.get("runs"))
            or not isinstance(state.get("exit_codes"), list)
            or not all(
                isinstance(code, int) and not isinstance(code, bool) for code in state["exit_codes"]
            )
            or len(state["exit_codes"]) != state["runs"]
        ):
            raise ValueError("refactor passport state observation is invalid")
        status = state["status"]
        exit_codes = state["exit_codes"]
        if (
            (status == "NOT_RUN" and (state["runs"] != 0 or exit_codes))
            or (status != "NOT_RUN" and state["runs"] == 0)
            or (status == "PASS" and any(code != 0 for code in exit_codes))
            or (status == "FAIL" and any(code != 1 for code in exit_codes))
            or (
                status == "FLAKY"
                and (
                    any(code not in {0, 1} for code in exit_codes)
                    or all(code == 0 for code in exit_codes)
                )
            )
        ):
            raise ValueError("refactor passport state status contradicts its exit codes")
    base_status = states["base"]["status"]
    target_status = states["target"]["status"]
    verdict = subject["verdict"]
    statuses = {base_status, target_status}
    derived_execution = (
        "FAILED" if "INFRA" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "COMPLETED"
    )
    if subject["truth"]["execution"] != derived_execution:
        raise ValueError("refactor passport execution truth contradicts its states")
    if any(state["runs"] != subject["reruns_per_state"] for state in states.values()):
        raise ValueError("refactor passport state runs do not match its rerun count")
    if subject["deterministic"] is True and not statuses.issubset({"PASS", "FAIL"}):
        raise ValueError("refactor deterministic summary contains an unstable state")
    if verdict == "VERIFIED" and not (
        subject["deterministic"] is True and base_status == "PASS" and target_status == "PASS"
    ):
        raise ValueError("refactor VERIFIED summary is inconsistent")
    if verdict == "PARTIAL" and not (
        subject["deterministic"] is True and base_status == "FAIL" and target_status == "FAIL"
    ):
        raise ValueError("refactor PARTIAL summary is inconsistent")
    if verdict == "FAILED" and (
        subject["deterministic"] is not True or (base_status == "PASS" and target_status == "PASS")
    ):
        raise ValueError("refactor FAILED summary is inconsistent")
    if verdict == "UNCERTAIN" and subject["deterministic"] is not False:
        raise ValueError("refactor UNCERTAIN summary is inconsistent")
    if verdict == "UNCERTAIN" and (
        base_status == "PASS"
        and target_status == "PASS"
        and all(state["runs"] >= subject["reruns_per_state"] for state in states.values())
    ):
        raise ValueError("refactor UNCERTAIN summary contradicts complete passing states")
    sources = subject.get("evidence_sources")
    if (
        not isinstance(sources, list)
        or len(sources) > 1000
        or not all(_valid_evidence_source(item) for item in sources)
    ):
        raise ValueError("refactor passport evidence sources are invalid")


def _validate_truth(
    value: object,
    *,
    expected_goal: str,
    expected_execution: str | None,
    expected_release: str,
) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"execution", "goal", "release"}
        or value.get("execution") not in _EXECUTION
        or (expected_execution is not None and value.get("execution") != expected_execution)
        or value.get("goal") != expected_goal
        or value.get("release") != expected_release
    ):
        raise ValueError("passport truth separation is invalid")


def _valid_proposal(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "provider",
        "requested_model",
        "confirmed_model",
        "confirmed_version",
        "output_sha256",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "tool_call_count",
    }:
        return False
    return (
        value.get("operation") in {"propose", "refine"}
        and all(
            _identity(value.get(name))
            for name in ("provider", "requested_model", "confirmed_model", "confirmed_version")
        )
        and _sha256(value.get("output_sha256"))
        and all(
            item is None or _bounded_int(item, maximum=10**12)
            for item in (
                value.get("input_tokens"),
                value.get("output_tokens"),
                value.get("total_tokens"),
            )
        )
        and _bounded_int(value.get("tool_call_count"), maximum=10**9)
    )


def _valid_evidence_source(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "evidence_id",
        "connector_id",
        "connector_version",
        "capability",
        "source",
        "request_sha256",
        "response_sha256",
        "artifact_sha256",
        "content_sha256",
    }:
        return False
    return (
        all(
            _identity(value.get(name))
            for name in ("evidence_id", "connector_id", "connector_version", "capability")
        )
        and (
            value.get("source") == "local-checkout"
            or _identity(value.get("source"), allow_unknown=False)
        )
        and all(
            _sha256(value.get(name))
            for name in ("request_sha256", "response_sha256", "artifact_sha256", "content_sha256")
        )
    )


def _identity(value: object, *, allow_unknown: bool = True) -> bool:
    if allow_unknown and value in {"unknown_no_telemetry", "unknown_unverified_backend"}:
        return True
    return isinstance(value, str) and value.startswith("sha256:") and _sha256(value[7:])


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _bounded_int(value: object, *, minimum: int = 0, maximum: int = 1000) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def render_html_passport(passport: dict[str, Any]) -> str:
    """Render one already-verified passport with no scripts or external assets."""
    claim_type = _text(passport["claim_type"])
    subject = passport["subject"]
    verification = passport["verification"]
    verdict = _text(subject.get("verdict", "UNKNOWN"))
    truth = subject.get("truth", {})
    manifest = _text(verification.get("manifest_sha256", ""))
    replay = verification.get("execution_replayed")
    replay_label = "Not replayed" if replay is None else "Matched" if replay else "Mismatch"
    claim_label = "Bug flip" if claim_type == "bug_flip" else "Behavior-preserving refactor"
    details = _bug_details(subject) if claim_type == "bug_flip" else _refactor_details(subject)
    raw_json = html.escape(json.dumps(passport, ensure_ascii=False, indent=2, sort_keys=True))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>Exhibit A — {claim_label} passport</title>
  <style>{_STYLES}</style>
</head>
<body>
  <main>
    <header class="masthead">
      <div>
        <p class="eyebrow">Exhibit A / public evidence passport</p>
        <h1>{claim_label}</h1>
        <p class="dek">A credential-free record derived from signed, execution-grounded evidence.</p>
      </div>
      <div class="seal" aria-label="Verdict: {verdict}">
        <span>Verdict</span><strong>{verdict}</strong>
      </div>
    </header>

    <section class="chain" aria-labelledby="chain-title">
      <div class="chain-line" aria-hidden="true"></div>
      <div class="chain-copy">
        <p class="eyebrow" id="chain-title">Chain of custody</p>
        <code>{manifest}</code>
      </div>
      <dl class="chain-facts">
        <div><dt>Integrity</dt><dd>Verified</dd></div>
        <div><dt>Publisher MAC</dt><dd>Verified</dd></div>
        <div><dt>Execution replay</dt><dd>{replay_label}</dd></div>
      </dl>
    </section>

    <section class="truth" aria-labelledby="truth-title">
      <div class="section-heading">
        <p class="eyebrow">Independent conclusions</p>
        <h2 id="truth-title">Three truths, kept separate</h2>
      </div>
      <div class="truth-grid">
        {_truth_card("Execution", truth.get("execution"), "Did the evidence process finish?")}
        {_truth_card("Goal", truth.get("goal"), "Did the checked claim hold?")}
        {_truth_card("Release", truth.get("release"), "Was shipping safety assessed?")}
      </div>
    </section>

    {details}

    <section class="raw" aria-labelledby="raw-title">
      <div class="section-heading">
        <p class="eyebrow">Machine record</p>
        <h2 id="raw-title">Signed JSON payload</h2>
      </div>
      <details>
        <summary>Inspect the complete sanitized passport</summary>
        <pre>{raw_json}</pre>
      </details>
    </section>

    <footer>
      <span>{PASSPORT_SCHEMA}</span>
      <span>No source, logs, credentials, or executable code embedded</span>
    </footer>
  </main>
</body>
</html>
"""


def _bug_details(subject: dict[str, Any]) -> str:
    proposals = subject.get("proposal_runs", [])
    sources = subject.get("evidence_sources", [])
    return f"""<section class="evidence" aria-labelledby="evidence-title">
      <div class="section-heading">
        <p class="eyebrow">Observed evidence</p>
        <h2 id="evidence-title">Deterministic fail-to-pass</h2>
      </div>
      <dl class="ledger">
        {_fact("Target reruns", subject.get("reruns"))}
        {_fact("Deterministic", subject.get("deterministic"))}
        {_fact("Test commitment", subject.get("test_sha256"), mono=True)}
        {_fact("Proposal records", len(proposals))}
        {_fact("Evidence sources", len(sources))}
      </dl>
      {_proposal_table(proposals)}
    </section>"""


def _refactor_details(subject: dict[str, Any]) -> str:
    states = subject.get("states", {})
    return f"""<section class="evidence" aria-labelledby="evidence-title">
      <div class="section-heading">
        <p class="eyebrow">Observed evidence</p>
        <h2 id="evidence-title">Two-state behavior record</h2>
      </div>
      <div class="state-grid">
        {_state_card("Base", states.get("base", {}))}
        {_state_card("Target", states.get("target", {}))}
      </div>
      <dl class="ledger">
        {_fact("Reruns per state", subject.get("reruns_per_state"))}
        {_fact("Deterministic", subject.get("deterministic"))}
        {_fact("Contract commitment", subject.get("contract_sha256"), mono=True)}
        {_fact("Evidence sources", len(subject.get("evidence_sources", [])))}
      </dl>
    </section>"""


def _truth_card(label: str, value: object, question: str) -> str:
    return f"""<article><span>{html.escape(label)}</span><strong>{_text(value)}</strong><p>{html.escape(question)}</p></article>"""


def _state_card(label: str, state: dict[str, Any]) -> str:
    exit_codes = ", ".join(str(code) for code in state.get("exit_codes", [])) or "—"
    return f"""<article><span>{html.escape(label)} state</span><strong>{_text(state.get("status"))}</strong><p>{_text(state.get("runs"))} runs · exit {html.escape(exit_codes)}</p></article>"""


def _fact(label: str, value: object, *, mono: bool = False) -> str:
    class_name = ' class="mono"' if mono else ""
    return f"<div><dt>{html.escape(label)}</dt><dd{class_name}>{_text(value)}</dd></div>"


def _proposal_table(proposals: list[dict[str, Any]]) -> str:
    if not proposals:
        return ""
    rows = "".join(
        f"<tr><td>{_text(item.get('operation'))}</td><td><code>{_text(item.get('provider'))}</code></td><td><code>{_text(item.get('confirmed_model'))}</code></td><td>{_text(item.get('total_tokens'))}</td></tr>"
        for item in proposals
    )
    return f"""<div class="table-wrap"><table><caption>Proposal provenance</caption><thead><tr><th>Operation</th><th>Provider</th><th>Confirmed model</th><th>Tokens</th></tr></thead><tbody>{rows}</tbody></table></div>"""


def _text(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return html.escape(str(value))


def _atomic_write(destination: Path, rendered: str) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


_STYLES = """
:root{--paper:#f7f8fb;--ink:#171b25;--muted:#5d6678;--line:#cbd2df;--blue:#1748c7;--blue-soft:#e7edff;--white:#fff;--ok:#1748c7;--shadow:0 18px 50px rgba(24,35,62,.08)}
*{box-sizing:border-box}html{background:#e8ebf1;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}body{margin:0;padding:40px 20px}main{width:min(1040px,100%);margin:auto;background:var(--paper);border:1px solid #bfc7d5;box-shadow:var(--shadow)}.masthead{display:grid;grid-template-columns:1fr auto;gap:40px;padding:52px 56px 44px;border-bottom:1px solid var(--line)}.eyebrow{margin:0 0 12px;color:var(--blue);font:700 11px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.14em;text-transform:uppercase}h1,h2{margin:0;font-family:Georgia,"Times New Roman",serif;font-weight:500}h1{font-size:clamp(42px,7vw,78px);line-height:.95;letter-spacing:-.045em;max-width:680px}h2{font-size:30px;letter-spacing:-.025em}.dek{max-width:580px;margin:22px 0 0;color:var(--muted);font-size:17px;line-height:1.6}.seal{width:148px;height:148px;border:2px solid var(--blue);border-radius:50%;display:grid;place-content:center;text-align:center;transform:rotate(4deg);background:var(--blue-soft);box-shadow:inset 0 0 0 7px var(--paper),inset 0 0 0 8px var(--blue)}.seal span{font:700 10px/1 ui-monospace,monospace;letter-spacing:.15em;text-transform:uppercase}.seal strong{display:block;margin-top:8px;color:var(--blue);font:800 16px/1.1 ui-monospace,monospace}.chain{position:relative;display:grid;grid-template-columns:minmax(0,1.5fr) 1fr;gap:40px;padding:34px 56px;background:var(--ink);color:var(--white)}.chain-line{position:absolute;left:0;top:0;bottom:0;width:7px;background:var(--blue)}.chain code{display:block;overflow-wrap:anywhere;color:#d9e2ff;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}.chain-facts{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:0}.chain-facts div{border-left:1px solid #4b5362;padding-left:12px}.chain-facts dt{color:#99a4b8;font-size:11px;line-height:1.3}.chain-facts dd{margin:6px 0 0;font:700 12px/1.2 ui-monospace,monospace}.truth,.evidence,.raw{padding:48px 56px;border-bottom:1px solid var(--line)}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:30px;margin-bottom:28px}.section-heading .eyebrow{margin:0}.truth-grid,.state-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}.truth-grid article,.state-grid article{min-width:0;padding:24px;background:var(--white)}.truth-grid span,.state-grid span{color:var(--muted);font-size:12px}.truth-grid strong,.state-grid strong{display:block;margin:14px 0 10px;color:var(--blue);font:800 18px/1.2 ui-monospace,monospace;overflow-wrap:anywhere}.truth-grid p,.state-grid p{margin:0;color:var(--muted);font-size:13px;line-height:1.45}.state-grid{grid-template-columns:repeat(2,1fr);margin-bottom:24px}.ledger{display:grid;grid-template-columns:repeat(2,1fr);margin:0;border-top:1px solid var(--line)}.ledger div{display:grid;grid-template-columns:150px minmax(0,1fr);gap:18px;padding:15px 0;border-bottom:1px solid var(--line)}.ledger div:nth-child(odd){padding-right:24px}.ledger div:nth-child(even){padding-left:24px;border-left:1px solid var(--line)}.ledger dt{color:var(--muted);font-size:12px}.ledger dd{margin:0;font-size:13px;font-weight:700;overflow-wrap:anywhere}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px!important}.table-wrap{margin-top:28px;overflow-x:auto}table{width:100%;border-collapse:collapse;background:var(--white);font-size:12px}caption{text-align:left;padding:0 0 10px;font-weight:700}th,td{padding:12px 14px;border:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase}td code{font-size:10px;overflow-wrap:anywhere}.raw details{border:1px solid var(--line);background:var(--white)}.raw summary{cursor:pointer;padding:16px 18px;font-weight:700}.raw summary:focus-visible{outline:3px solid var(--blue);outline-offset:3px}.raw pre{max-height:480px;margin:0;padding:18px;border-top:1px solid var(--line);overflow:auto;background:#111722;color:#dbe4f5;font:11px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}footer{display:flex;justify-content:space-between;gap:20px;padding:22px 56px;color:var(--muted);font:10px/1.4 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em}
@media(max-width:760px){body{padding:0}main{border:0}.masthead{grid-template-columns:1fr;padding:36px 24px}.seal{width:118px;height:118px}.chain{grid-template-columns:1fr;padding:28px 24px}.chain-facts{grid-template-columns:1fr}.truth,.evidence,.raw{padding:36px 24px}.section-heading{display:block}.section-heading .eyebrow{margin-bottom:10px}.truth-grid,.state-grid,.ledger{grid-template-columns:1fr}.ledger div,.ledger div:nth-child(odd),.ledger div:nth-child(even){padding:14px 0;border-left:0}.ledger div{grid-template-columns:125px minmax(0,1fr)}footer{display:grid;padding:20px 24px}}
@media print{html{background:white}body{padding:0}main{border:0;box-shadow:none}.raw{break-before:page}.raw details{display:block}.raw summary{display:none}.raw pre{max-height:none}.seal{box-shadow:inset 0 0 0 7px white,inset 0 0 0 8px var(--blue)}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
"""
