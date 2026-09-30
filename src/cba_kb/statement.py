"""Statement contract, deterministic extraction, and automatic verification routing.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D01:
- statement_id: deterministic hash
- doc_id: validated doc_id
- speaker_actor_ref: ActorRef
- subject_actor_refs: list of ActorRefs
- time_anchor: dict or str
- statement_text_or_controlled_excerpt: non-empty str
- source_ref: str / dict locator
- evidence_ref: dict / str
- attribution_type: structured_turn | direct_quote | indirect_attribution
- rights: dict
- provenance: dict or str
- extraction_status: accepted | review_required

Invariants:
- Statement occurrence is source-bound and deterministic.
- Direct quote vs narrator separation is strict: narrator text cannot be attributed to quoted speaker.
- Ambiguous attribution -> review_required and automatic Verification Queue routing (R2).
- Same-name subjects must not assert multiple canonical player identities (R3).
- Arbitrary caller-supplied doc_id cannot bypass intake binding (R5).
- Ordering and replay are deterministic for identical input.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple, Set

from .actor import ActorError, validate_actor_ref, resolve_actor, make_actor_ref
from .document_lane import doc_id as compute_doc_id
from .document_mentions import validate_doc_id
from .evidence_ledger import canonical_bytes
from .verification_queue import VerificationQueue

SCHEMA_VERSION = 1
STATEMENT_VERSION = "v1.9"

ATTRIBUTION_TYPES = frozenset({
    "structured_turn",
    "direct_quote",
    "indirect_attribution",
})

EXTRACTION_STATUSES = frozenset({
    "accepted",
    "review_required",
})

_STATEMENT_ID = re.compile(r"stmt_[0-9a-f]{24}\Z")

# Regular expression for structured dialogue turns:
# e.g.: "顾全：那场比赛确实打得很艰难。" or "【孟铎】我们从小一起长大。" or "Q: 怎么看待转会？"
_STRUCTURED_TURN_RE = re.compile(
    r"^(?:【(?P<bracket_spk>[^】\n]+)】|(?P<prefix_spk>[\w\u4e00-\u9fa5·]{2,12})[：:])\s*(?P<content>.+)$"
)

# Regular expression for direct quote detection:
# e.g.: 贺希宁在接受采访时表示：“我们全队都非常渴望赢下这场比赛。”
# or: “回到深圳就像回家一样，”沈梓捷说。
_QUOTE_PATTERNS = [
    re.compile(
        r"(?P<context>(?:(?P<speaker>[\w\u4e00-\u9fa5·]{2,12})[，,\s]*)?(?:在[^，。\n]{0,20})?(?:说|表示|直言|坦言|回忆|强调|回应|谈到|透露|指出|称|道))[：:]\s*[“\"](?P<quote>[^”\"\n]+)[”\"]"
    ),
    re.compile(
        r"[“\"](?P<quote>[^”\"\n]+)[”\"][，,\s]*(?P<speaker>[\w\u4e00-\u9fa5·]{2,12})[，,\s]*(?:说|表示|直言|坦言|回忆|强调|回应|谈到|指出|称|道)"
    ),
]


class StatementError(RuntimeError):
    pass


class ExtractionResult(list):
    """List-compatible container for extracted statements and automatic verification items (R2)."""

    def __init__(
        self,
        statements: List[Dict[str, Any]],
        verification_items: List[Dict[str, Any]],
        queue: VerificationQueue,
    ):
        super().__init__(statements)
        self.statements = statements
        self.verification_items = verification_items
        self.queue = queue


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StatementError(f"{label}_REQUIRED")
    if "\x00" in value:
        raise StatementError(f"{label}_INVALID")
    return value.strip()


def validate_statement_id(value: str) -> str:
    value = _required_text(value, "STATEMENT_ID")
    if not _STATEMENT_ID.fullmatch(value):
        raise StatementError("STATEMENT_ID_FORMAT_INVALID")
    return value


def verify_document_binding(text: str, doc_id: str) -> str:
    """Verify that caller-supplied doc_id binds strictly to normalized content (R5)."""
    expected_doc_id = compute_doc_id(text)
    if doc_id != expected_doc_id:
        raise StatementError(
            f"DOC_ID_CONTENT_MISMATCH: expected {expected_doc_id} but got {doc_id}"
        )
    return doc_id


def generate_statement_id(
    doc_id: str,
    ordinal: int,
    statement_text: str,
    speaker_actor_ref: Dict[str, Any],
    attribution_type: str,
) -> str:
    """Generate a deterministic statement_id."""
    payload = {
        "doc_id": doc_id,
        "ordinal": ordinal,
        "statement_text": statement_text.strip(),
        "speaker_kind": speaker_actor_ref.get("kind"),
        "speaker_id": speaker_actor_ref.get("id"),
        "speaker_name": speaker_actor_ref.get("raw_name"),
        "attribution_type": attribution_type,
    }
    digest = hashlib.sha256(canonical_bytes(payload)).hexdigest()[:24]
    return f"stmt_{digest}"


def validate_statement(statement: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a statement dict."""
    if not isinstance(statement, dict):
        raise StatementError("STATEMENT_OBJECT_REQUIRED")

    required = {
        "statement_id",
        "doc_id",
        "speaker_actor_ref",
        "subject_actor_refs",
        "time_anchor",
        "statement_text_or_controlled_excerpt",
        "source_ref",
        "evidence_ref",
        "attribution_type",
        "rights",
        "provenance",
        "extraction_status",
    }
    missing = required - set(statement)
    if missing:
        raise StatementError(f"STATEMENT_FIELDS_MISSING:{sorted(missing)}")

    statement_id = validate_statement_id(statement["statement_id"])
    try:
        doc_id = validate_doc_id(statement["doc_id"])
    except Exception as exc:
        raise StatementError("DOC_ID_INVALID") from exc

    try:
        speaker_actor_ref = validate_actor_ref(statement["speaker_actor_ref"])
    except ActorError as exc:
        raise StatementError("SPEAKER_ACTOR_REF_INVALID") from exc

    if not isinstance(statement["subject_actor_refs"], list):
        raise StatementError("SUBJECT_ACTOR_REFS_LIST_REQUIRED")
    subject_actor_refs = []
    for sub in statement["subject_actor_refs"]:
        try:
            subject_actor_refs.append(validate_actor_ref(sub))
        except ActorError as exc:
            raise StatementError("SUBJECT_ACTOR_REF_INVALID") from exc

    time_anchor = statement["time_anchor"]
    if time_anchor in (None, "", {}):
        raise StatementError("TIME_ANCHOR_REQUIRED")

    text = _required_text(
        statement["statement_text_or_controlled_excerpt"],
        "STATEMENT_TEXT",
    )

    source_ref = statement["source_ref"]
    if source_ref in (None, "", {}):
        raise StatementError("SOURCE_REF_REQUIRED")

    evidence_ref = statement["evidence_ref"]
    if evidence_ref in (None, "", {}):
        raise StatementError("EVIDENCE_REF_REQUIRED")

    attribution_type = _required_text(statement["attribution_type"], "ATTRIBUTION_TYPE")
    if attribution_type not in ATTRIBUTION_TYPES:
        raise StatementError("ATTRIBUTION_TYPE_INVALID")

    rights = statement["rights"]
    if not isinstance(rights, dict) or "classification" not in rights:
        raise StatementError("RIGHTS_OBJECT_REQUIRED")

    provenance = statement["provenance"]
    if provenance in (None, "", {}):
        raise StatementError("PROVENANCE_REQUIRED")

    extraction_status = _required_text(statement["extraction_status"], "EXTRACTION_STATUS")
    if extraction_status not in EXTRACTION_STATUSES:
        raise StatementError("EXTRACTION_STATUS_INVALID")

    # Safety checks
    if speaker_actor_ref["kind"] == "unresolved" and speaker_actor_ref["raw_name"] in {"UNKNOWN", "有人", "外界"}:
        if extraction_status == "accepted":
            raise StatementError("AMBIGUOUS_SPEAKER_CANNOT_BE_ACCEPTED")

    return {
        "statement_id": statement_id,
        "doc_id": doc_id,
        "speaker_actor_ref": speaker_actor_ref,
        "subject_actor_refs": subject_actor_refs,
        "time_anchor": time_anchor,
        "statement_text_or_controlled_excerpt": text,
        "source_ref": source_ref,
        "evidence_ref": evidence_ref,
        "attribution_type": attribution_type,
        "rights": rights,
        "provenance": provenance,
        "extraction_status": extraction_status,
    }


def _is_same_name_ambiguity(name: str, identity_registry: Dict[str, Any]) -> bool:
    """Check if name matches multiple active players (R3)."""
    players = identity_registry.get("players", [])
    matches = sum(
        1 for p in players
        if (p.get("status") == "ACTIVE" or p.get("active") is True)
        and p.get("canonical_name") == name
    )
    return matches > 1


def _find_subject_mentions(
    text: str,
    *,
    identity_registry: Optional[Dict[str, Any]],
    person_registry: Optional[Dict[str, Any]],
    speaker_raw_name: str,
    doc_id: str,
    ordinal: int,
    queue: Optional[VerificationQueue] = None,
) -> List[Dict[str, Any]]:
    """Find actors mentioned in the statement text as subjects with uniqueness enforcement (R3).

    Invariants:
    - Same-name must never assert multiple canonical player identities.
    - Ambiguous same-name mentions resolve to kind='unresolved', id=None and emit a Verification Queue item.
    """
    subjects: List[Dict[str, Any]] = []
    if not identity_registry:
        return subjects

    players = identity_registry.get("players", [])
    aliases = identity_registry.get("aliases", [])

    candidate_names: Set[str] = set()
    for p in players:
        cname = p.get("canonical_name")
        if cname and cname != speaker_raw_name and cname in text:
            candidate_names.add(cname)

    for a in aliases:
        aname = a.get("alias_name")
        if aname and aname != speaker_raw_name and aname in text:
            candidate_names.add(aname)

    for cand_name in sorted(candidate_names):
        actor_ref = resolve_actor(
            cand_name,
            evidence_ref=f"{doc_id}#turn-{ordinal}",
            identity_registry=identity_registry,
            person_registry=person_registry,
        )

        if actor_ref["kind"] == "unresolved":
            is_ambig = _is_same_name_ambiguity(cand_name, identity_registry)
            reason = "AMBIGUOUS_SAME_NAME_ACTOR" if is_ambig else "UNRESOLVED_SUBJECT_ACTOR"
            if queue:
                queue.create_and_add(
                    object_type="actor",
                    object_ref={"raw_name": cand_name, "doc_id": doc_id, "turn": ordinal},
                    reason_code=reason,
                    evidence_refs=[f"{doc_id}#turn-{ordinal}"],
                    created_from="statement_extractor",
                )
        subjects.append(actor_ref)

    return sorted(subjects, key=lambda s: s["raw_name"])


def extract_statements_from_text(
    text: str,
    doc_id: Optional[str] = None,
    *,
    identity_registry: Optional[Dict[str, Any]] = None,
    person_registry: Optional[Dict[str, Any]] = None,
    rights: Optional[Dict[str, Any]] = None,
    provenance: Optional[Any] = None,
    default_time_anchor: Any = "UNKNOWN",
    queue: Optional[VerificationQueue] = None,
    verify_binding: bool = True,
) -> ExtractionResult:
    """Extract deterministic statements from document text with automatic verification routing (R2, R3, R5).

    Supports:
    1. Structured dialogue turns (顾全：..., 孟铎: ...) -> structured_turn
    2. Direct quotations in narrative (沈梓捷表示：“...”) -> direct_quote
    3. Indirect/ambiguous statements (据透露...) -> indirect_attribution

    Returns ExtractionResult (a list of statements with .verification_items and .queue).
    """
    if doc_id is None:
        doc_id = compute_doc_id(text)
    elif verify_binding:
        verify_document_binding(text, doc_id)

    validate_doc_id(doc_id)
    rights = rights or {"classification": "private", "public_export_allowed": False, "evidence": []}
    provenance = provenance or {"extracted_at": "1970-01-01T00:00:00Z", "doc_id": doc_id}

    if queue is None:
        queue = VerificationQueue()

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    statements: List[Dict[str, Any]] = []
    ordinal = 0

    for line_idx, line in enumerate(lines, start=1):
        # 1. Check for structured dialogue turns or leading quote clauses
        m_turn = _STRUCTURED_TURN_RE.match(line)
        if m_turn:
            speaker_str = (m_turn.group("bracket_spk") or m_turn.group("prefix_spk")).strip()
            content = m_turn.group("content").strip()

            speech_verbs = ("说", "表示", "直言", "坦言", "回忆", "强调", "回应", "谈到", "指出", "称", "道")
            is_quote = (content.startswith(('“', '"')) and content.endswith(('”', '"'))) or any(speaker_str.endswith(v) for v in speech_verbs)

            if is_quote:
                clean_speaker = speaker_str
                for v in speech_verbs:
                    if clean_speaker.endswith(v) and len(clean_speaker) > len(v):
                        clean_speaker = clean_speaker[:-len(v)].strip()
                        break
                quote_text = content.strip('“”"\' ')
                ordinal += 1
                speaker_ref = resolve_actor(
                    clean_speaker,
                    evidence_ref=f"{doc_id}#L{line_idx}",
                    identity_registry=identity_registry,
                    person_registry=person_registry,
                )
                stmt_id = generate_statement_id(
                    doc_id=doc_id,
                    ordinal=ordinal,
                    statement_text=quote_text,
                    speaker_actor_ref=speaker_ref,
                    attribution_type="direct_quote",
                )
                subjects = _find_subject_mentions(
                    quote_text,
                    identity_registry=identity_registry,
                    person_registry=person_registry,
                    speaker_raw_name=clean_speaker,
                    doc_id=doc_id,
                    ordinal=ordinal,
                    queue=queue,
                )
                is_accepted = (speaker_ref["kind"] != "unresolved" or clean_speaker not in {"UNKNOWN", "有人", "外界"})
                extraction_status = "accepted" if is_accepted else "review_required"

                evidence_ref = {
                    "line_number": line_idx,
                    "locator": f"{doc_id}#L{line_idx}",
                }
                stmt = validate_statement({
                    "statement_id": stmt_id,
                    "doc_id": doc_id,
                    "speaker_actor_ref": speaker_ref,
                    "subject_actor_refs": subjects,
                    "time_anchor": default_time_anchor,
                    "statement_text_or_controlled_excerpt": quote_text,
                    "source_ref": f"{doc_id}#quote-{ordinal}",
                    "evidence_ref": evidence_ref,
                    "attribution_type": "direct_quote",
                    "rights": rights,
                    "provenance": provenance,
                    "extraction_status": extraction_status,
                })
                statements.append(stmt)

                # Automatic verification queue routing (R2)
                if speaker_ref["kind"] == "unresolved":
                    queue.create_and_add(
                        object_type="actor",
                        object_ref={"raw_name": clean_speaker, "statement_id": stmt_id},
                        reason_code="UNRESOLVED_SPEAKER_ACTOR",
                        evidence_refs=[f"{doc_id}#L{line_idx}"],
                        created_from="statement_extractor",
                    )
                if extraction_status == "review_required":
                    queue.create_and_add(
                        object_type="statement",
                        object_ref=stmt_id,
                        reason_code="AMBIGUOUS_ATTRIBUTION",
                        evidence_refs=[f"{doc_id}#L{line_idx}"],
                        created_from="statement_extractor",
                    )
                continue

            ordinal += 1
            speaker_ref = resolve_actor(
                speaker_str,
                evidence_ref=f"{doc_id}#L{line_idx}",
                identity_registry=identity_registry,
                person_registry=person_registry,
            )

            status = "accepted"
            if speaker_str in {"问", "Q", "记者", "主持人", "主持人说", "网传"}:
                status = "review_required"

            stmt_id = generate_statement_id(
                doc_id=doc_id,
                ordinal=ordinal,
                statement_text=content,
                speaker_actor_ref=speaker_ref,
                attribution_type="structured_turn",
            )

            subjects = _find_subject_mentions(
                content,
                identity_registry=identity_registry,
                person_registry=person_registry,
                speaker_raw_name=speaker_str,
                doc_id=doc_id,
                ordinal=ordinal,
                queue=queue,
            )

            stmt = validate_statement({
                "statement_id": stmt_id,
                "doc_id": doc_id,
                "speaker_actor_ref": speaker_ref,
                "subject_actor_refs": subjects,
                "time_anchor": default_time_anchor,
                "statement_text_or_controlled_excerpt": content,
                "source_ref": f"{doc_id}#turn-{ordinal}",
                "evidence_ref": {
                    "line_number": line_idx,
                    "locator": f"{doc_id}#L{line_idx}",
                },
                "attribution_type": "structured_turn",
                "rights": rights,
                "provenance": provenance,
                "extraction_status": status,
            })
            statements.append(stmt)

            # Automatic verification queue routing (R2)
            if speaker_ref["kind"] == "unresolved":
                queue.create_and_add(
                    object_type="actor",
                    object_ref={"raw_name": speaker_str, "statement_id": stmt_id},
                    reason_code="UNRESOLVED_SPEAKER_ACTOR",
                    evidence_refs=[f"{doc_id}#L{line_idx}"],
                    created_from="statement_extractor",
                )
            if status == "review_required":
                queue.create_and_add(
                    object_type="statement",
                    object_ref=stmt_id,
                    reason_code="AMBIGUOUS_ATTRIBUTION",
                    evidence_refs=[f"{doc_id}#L{line_idx}"],
                    created_from="statement_extractor",
                )
            continue

        # 2. Check for direct quotes in narrative
        found_quote = False
        for pattern in _QUOTE_PATTERNS:
            for match in pattern.finditer(line):
                speaker_str = match.group("speaker") if "speaker" in match.groupdict() else None
                quote_text = match.group("quote").strip()
                if not quote_text:
                    continue

                found_quote = True
                ordinal += 1

                if speaker_str:
                    speaker_ref = resolve_actor(
                        speaker_str.strip(),
                        evidence_ref=f"{doc_id}#L{line_idx}",
                        identity_registry=identity_registry,
                        person_registry=person_registry,
                    )
                    status = "accepted"
                else:
                    speaker_ref = make_actor_ref(
                        kind="unresolved",
                        actor_id=None,
                        raw_name="UNKNOWN",
                        evidence_ref=f"{doc_id}#L{line_idx}",
                    )
                    status = "review_required"

                stmt_id = generate_statement_id(
                    doc_id=doc_id,
                    ordinal=ordinal,
                    statement_text=quote_text,
                    speaker_actor_ref=speaker_ref,
                    attribution_type="direct_quote",
                )

                subjects = _find_subject_mentions(
                    quote_text,
                    identity_registry=identity_registry,
                    person_registry=person_registry,
                    speaker_raw_name=speaker_str or "",
                    doc_id=doc_id,
                    ordinal=ordinal,
                    queue=queue,
                )

                stmt = validate_statement({
                    "statement_id": stmt_id,
                    "doc_id": doc_id,
                    "speaker_actor_ref": speaker_ref,
                    "subject_actor_refs": subjects,
                    "time_anchor": default_time_anchor,
                    "statement_text_or_controlled_excerpt": quote_text,
                    "source_ref": f"{doc_id}#quote-{ordinal}",
                    "evidence_ref": {
                        "line_number": line_idx,
                        "locator": f"{doc_id}#L{line_idx}",
                    },
                    "attribution_type": "direct_quote",
                    "rights": rights,
                    "provenance": provenance,
                    "extraction_status": status,
                })
                statements.append(stmt)

                # Automatic verification queue routing (R2)
                if speaker_ref["kind"] == "unresolved":
                    queue.create_and_add(
                        object_type="actor",
                        object_ref={"raw_name": speaker_str or "UNKNOWN", "statement_id": stmt_id},
                        reason_code="UNRESOLVED_SPEAKER_ACTOR",
                        evidence_refs=[f"{doc_id}#L{line_idx}"],
                        created_from="statement_extractor",
                    )
                if status == "review_required":
                    queue.create_and_add(
                        object_type="statement",
                        object_ref=stmt_id,
                        reason_code="AMBIGUOUS_ATTRIBUTION",
                        evidence_refs=[f"{doc_id}#L{line_idx}"],
                        created_from="statement_extractor",
                    )

        if found_quote:
            continue

        # 3. Check for indirect attribution signals
        if any(marker in line for marker in ("据透露", "据悉", "消息称", "据知情人士透露", "外界分析")):
            ordinal += 1
            speaker_ref = make_actor_ref(
                kind="unresolved",
                actor_id=None,
                raw_name="UNKNOWN",
                evidence_ref=f"{doc_id}#L{line_idx}",
            )
            stmt_id = generate_statement_id(
                doc_id=doc_id,
                ordinal=ordinal,
                statement_text=line,
                speaker_actor_ref=speaker_ref,
                attribution_type="indirect_attribution",
            )
            subjects = _find_subject_mentions(
                line,
                identity_registry=identity_registry,
                person_registry=person_registry,
                speaker_raw_name="",
                doc_id=doc_id,
                ordinal=ordinal,
                queue=queue,
            )
            stmt = validate_statement({
                "statement_id": stmt_id,
                "doc_id": doc_id,
                "speaker_actor_ref": speaker_ref,
                "subject_actor_refs": subjects,
                "time_anchor": default_time_anchor,
                "statement_text_or_controlled_excerpt": line,
                "source_ref": f"{doc_id}#indirect-{ordinal}",
                "evidence_ref": {
                    "line_number": line_idx,
                    "locator": f"{doc_id}#L{line_idx}",
                },
                "attribution_type": "indirect_attribution",
                "rights": rights,
                "provenance": provenance,
                "extraction_status": "review_required",
            })
            statements.append(stmt)

            # Automatic verification queue routing (R2)
            queue.create_and_add(
                object_type="statement",
                object_ref=stmt_id,
                reason_code="AMBIGUOUS_ATTRIBUTION",
                evidence_refs=[f"{doc_id}#L{line_idx}"],
                created_from="statement_extractor",
            )

    return ExtractionResult(statements, queue.list_items(), queue)


def extract_statements_with_queue(
    text: str,
    doc_id: Optional[str] = None,
    **kwargs,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convenience helper explicitly returning (statements, verification_items)."""
    res = extract_statements_from_text(text, doc_id=doc_id, **kwargs)
    return res.statements, res.verification_items
