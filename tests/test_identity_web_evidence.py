import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from cba_kb.identity_coverage import prepare_review_packet
from cba_kb.identity_web_evidence import (
    IdentityWebEvidenceError,
    batch_predicate,
    build_batch_plan,
    build_conflicts,
    build_discovery_record,
    build_evidence_item,
    build_evidence_manifest,
    build_groups,
    classify_record_evidence,
    collect_evidence_from_responses,
    deterministic_group_id,
    expand_batch_event,
    expand_group_event,
    export_review_workbook,
    external_person_id_claim,
    extract_html_claims,
    import_review_workbook,
    make_claim,
    normalize_claim,
    search_exhaustion_status,
    validate_fetch_request,
    validate_redirect,
    validate_source_url,
)


def proposal(record_key, proposal_type, relation, **overrides):
    value = {
        "record_key": record_key,
        "proposal_type": proposal_type,
        "candidate_group_id": None,
        "candidate_player_uid": None,
        "candidate_name_display_only": "Synthetic",
        "proposed_relation": relation,
        "machine_suggestion": relation,
        "machine_reason": "synthetic",
        "evidence_refs": ["synthetic-evidence"],
    }
    value.update(overrides)
    return value


def evidence_item(
    url,
    content,
    *,
    tier="A1",
    claims=None,
    record_keys=None,
    content_type="text/html",
):
    return build_evidence_item(
        source_tier=tier,
        source_kind="registration_notice",
        source_url=url,
        fetched_at="2026-09-18T00:00:00Z",
        http_status=200,
        content_type=content_type,
        content=content,
        raw_snapshot_path=f"raw/{hash(content)}.bin",
        claims=claims or [],
        record_keys=record_keys or [],
    )


def test_source_policy_and_redirect_guard():
    assert validate_source_url(
        "https://www.cba.net.cn/notice",
        source_tier="A0",
    )["domain"] == "www.cba.net.cn"
    with pytest.raises(IdentityWebEvidenceError, match="DOMAIN_NOT_ALLOWED"):
        validate_source_url(
            "https://evil.example/notice",
            source_tier="A0",
        )
    with pytest.raises(IdentityWebEvidenceError, match="B0_DOMAIN_NOT_APPROVED"):
        validate_source_url(
            "https://club.example/notice",
            source_tier="B0",
            private_b0_domains=["official-club.example"],
        )
    with pytest.raises(IdentityWebEvidenceError, match="DOMAIN_NOT_ALLOWED"):
        validate_redirect(
            "https://www.cbaleague.com/a",
            "https://evil.example/b",
            source_tier="A1",
        )


def test_fetch_request_rejects_authenticated_paths_and_headers():
    with pytest.raises(IdentityWebEvidenceError, match="AUTHENTICATED_HEADERS"):
        validate_fetch_request(
            "https://www.cbaleague.com/player",
            headers={"Cookie": "session=1"},
        )
    with pytest.raises(IdentityWebEvidenceError, match="AUTHENTICATED_RESOURCE"):
        validate_source_url(
            "https://www.cbaleague.com/login",
            source_tier="A1",
        )
    assert validate_fetch_request(
        "https://www.cbaleague.com/player",
    )["method"] == "GET"


def test_external_person_id_requires_semantics_and_namespace():
    claim = external_person_id_claim(
        namespace="CBA_OFFICIAL_PLAYER_ID",
        identifier="123",
        source_semantics="PLAYER_ENTITY",
        source_locator="json.player.id",
    )
    assert claim["normalized_value"] == "CBA_OFFICIAL_PLAYER_ID:123"
    with pytest.raises(IdentityWebEvidenceError, match="SEMANTICS_REQUIRED"):
        external_person_id_claim(
            namespace="CBA_OFFICIAL_PLAYER_ID",
            identifier="123",
            source_semantics="ARTICLE_ID",
            source_locator="article.id",
        )


def test_evidence_ids_are_deterministic_and_change_with_bytes():
    first = evidence_item(
        "https://www.cbaleague.com/player/1",
        b"<html>one</html>",
    )
    second = evidence_item(
        "https://www.cbaleague.com/player/1",
        b"<html>one</html>",
    )
    changed = evidence_item(
        "https://www.cbaleague.com/player/1",
        b"<html>two</html>",
    )
    assert first["evidence_id"] == second["evidence_id"]
    assert changed["evidence_id"] != first["evidence_id"]


def test_html_claim_extraction_and_normalization():
    claims = extract_html_claims(
        b'<span data-claim-type="OFFICIAL_PLAYER_NAME" '
        b'data-claim-value="  Synthetic  Player  " '
        b'data-claim-locator="player-name"></span>'
    )
    assert claims[0]["normalized_value"] == "Synthetic Player"
    assert normalize_claim("OFFICIAL_BIRTH_DATE", "2000/01/02") == "2000-01-02"


def test_evidence_classification_w1_w2_w3_w4_wx():
    w1 = build_evidence_manifest([
        evidence_item(
            "https://www.cbaleague.com/player/1",
            b"w1",
            claims=[external_person_id_claim(
                namespace="CBA_OFFICIAL_PLAYER_ID",
                identifier="1",
                source_semantics="PLAYER_ENTITY",
                source_locator="player.id",
            )],
            record_keys=["r1"],
        )
    ])
    assert classify_record_evidence("r1", w1)[0] == (
        "W1_OFFICIAL_PERSON_ID_EXACT"
    )

    w2 = build_evidence_manifest([
        evidence_item(
            "https://www.cbaleague.com/player/1",
            b"w2a",
            claims=[
                make_claim(
                    claim_type="OFFICIAL_PLAYER_NAME",
                    raw_value="Synthetic Player",
                    source_locator="name",
                ),
                make_claim(
                    claim_type="OFFICIAL_BIRTH_DATE",
                    raw_value="2000-01-02",
                    source_locator="dob",
                ),
            ],
            record_keys=["r2"],
        ),
        evidence_item(
            "https://www.cbaleague.com/player/2",
            b"w2b",
            claims=[
                make_claim(
                    claim_type="OFFICIAL_PLAYER_NAME",
                    raw_value="Synthetic Player",
                    source_locator="name",
                ),
                make_claim(
                    claim_type="OFFICIAL_TEAM",
                    raw_value="Synthetic Club",
                    source_locator="team",
                ),
            ],
            record_keys=["r2"],
        ),
    ])
    assert classify_record_evidence("r2", w2)[0] == (
        "W2_OFFICIAL_BIO_MULTI_SOURCE"
    )

    w3 = build_evidence_manifest([
        evidence_item(
            "https://www.cbaleague.com/player/3",
            b"w3",
            claims=[
                make_claim(
                    claim_type="OFFICIAL_PLAYER_NAME",
                    raw_value="Synthetic Player",
                    source_locator="name",
                ),
                make_claim(
                    claim_type="OFFICIAL_TEAM",
                    raw_value="Synthetic Club",
                    source_locator="team",
                ),
            ],
            record_keys=["r3"],
        )
    ])
    assert classify_record_evidence("r3", w3)[0] == (
        "W3_OFFICIAL_CONTINUITY"
    )
    w4 = build_evidence_manifest([
        evidence_item(
            "https://www.cbaleague.com/discovery",
            b"w4",
            record_keys=["r4"],
        )
    ])
    assert classify_record_evidence("r4", w4)[0] == (
        "W4_DISCOVERY_SUPPORT"
    )

    wx = build_evidence_manifest([
        evidence_item(
            "https://www.cbaleague.com/player/4",
            b"wx-a",
            claims=[external_person_id_claim(
                namespace="CBA_OFFICIAL_PLAYER_ID",
                identifier="4",
                source_semantics="PLAYER_ENTITY",
                source_locator="id",
            )],
            record_keys=["r5"],
        ),
        evidence_item(
            "https://www.cbaleague.com/player/5",
            b"wx-b",
            claims=[external_person_id_claim(
                namespace="CBA_OFFICIAL_PLAYER_ID",
                identifier="5",
                source_semantics="PLAYER_ENTITY",
                source_locator="id",
            )],
            record_keys=["r5"],
        ),
    ])
    assert classify_record_evidence("r5", wx)[0] == "WX_CONFLICT"
    assert build_conflicts(wx)[0]["record_key"] == "r5"


def test_search_exhaustion_requires_complete_clean_collectors():
    assert search_exhaustion_status(
        collector_failures=0,
        surviving_official_candidate=False,
        text_warning=False,
        identity_conflict=False,
    ) == "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"
    assert search_exhaustion_status(
        collector_failures=1,
        surviving_official_candidate=False,
        text_warning=False,
        identity_conflict=False,
    ) != "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"
    assert search_exhaustion_status(
        collector_failures=0,
        surviving_official_candidate=False,
        text_warning=True,
        identity_conflict=False,
    ) != "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"


def test_group_ids_deterministic_and_group_event_exact_membership():
    members = ["review_b", "review_a"]
    assert deterministic_group_id(
        members,
        "W1_OFFICIAL_PERSON_ID_EXACT",
    ) == deterministic_group_id(
        reversed(members),
        "W1_OFFICIAL_PERSON_ID_EXACT",
    )
    packet = prepare_review_packet([
        proposal(
            "r1",
            "NEW_IDENTITY_CANDIDATE",
            "PROPOSED_SAME",
            candidate_group_id="group-1",
        ),
        proposal(
            "r2",
            "NEW_IDENTITY_CANDIDATE",
            "PROPOSED_SAME",
            candidate_group_id="group-1",
        ),
    ])
    groups = [{
        "candidate_group_id": "group-1",
        "member_review_ids": sorted(
            item["review_id"] for item in packet["reviews"]
        ),
        "member_count": 2,
        "evidence_class": "W1_OFFICIAL_PERSON_ID_EXACT",
        "conflict_count": 0,
    }]
    manifest = build_evidence_manifest([])
    event = {
        "event_id": "event-1",
        "candidate_group_id": "group-1",
        "review_packet_sha256": packet["review_packet_sha256"],
        "web_evidence_manifest_sha256": manifest[
            "web_evidence_manifest_sha256"
        ],
        "member_review_ids": groups[0]["member_review_ids"],
        "human_decision": "APPROVE",
        "human_note": "approved group",
        "approved_player_uid": "pid_0000000000000099",
        "approved_canonical_name": "Synthetic Player",
        "allocation_attestation": "allocated independently",
        "reviewed_at": "2026-09-18T00:00:00Z",
    }
    expanded = expand_group_event(packet, manifest, groups, event)
    assert len(expanded) == 2
    assert all(item["approved_player_uid"] == "pid_0000000000000099" for item in expanded)
    with pytest.raises(IdentityWebEvidenceError, match="MEMBERSHIP_MISMATCH"):
        expand_group_event(
            packet,
            manifest,
            groups,
            {**event, "member_review_ids": groups[0]["member_review_ids"][:1]},
        )


def test_batch_predicate_and_event_expansion():
    assert batch_predicate({
        "proposal_type": "EXISTING_IDENTITY_CANDIDATE",
        "evidence_class": "W1_OFFICIAL_PERSON_ID_EXACT",
        "conflict_count": 0,
        "text_corruption_warning": False,
    }) == "BATCH_EXISTING_W1"
    assert batch_predicate({
        "proposal_type": "EXISTING_IDENTITY_CANDIDATE",
        "evidence_class": "W2_OFFICIAL_BIO_MULTI_SOURCE",
        "conflict_count": 1,
        "text_corruption_warning": False,
    }) is None
    packet = prepare_review_packet([
        proposal(
            "r1",
            "EXISTING_IDENTITY_CANDIDATE",
            "KEEP_UNDECIDED",
            candidate_player_uid="pid_0000000000000001",
        )
    ])
    review_id = packet["reviews"][0]["review_id"]
    batches = [{
        "batch_id": "batch-1",
        "batch_predicate": "BATCH_EXISTING_W1",
        "member_review_ids": [review_id],
        "member_count": 1,
    }]
    manifest = build_evidence_manifest([])
    event = {
        "event_id": "batch-event-1",
        "batch_id": "batch-1",
        "review_packet_sha256": packet["review_packet_sha256"],
        "web_evidence_manifest_sha256": manifest[
            "web_evidence_manifest_sha256"
        ],
        "member_review_ids": [review_id],
        "batch_predicate": "BATCH_EXISTING_W1",
        "human_decision": "APPROVE",
        "human_note": "approved batch",
        "reviewed_at": "2026-09-18T00:00:00Z",
    }
    expanded = expand_batch_event(packet, manifest, batches, event)
    assert expanded[0]["human_note"] == "approved batch"
    assert expanded[0]["approved_player_uid"] is None


def test_collect_html_and_pdf_fixtures(tmp_path):
    discovery = build_discovery_record(
        query="Synthetic Player official",
        discovered_url="https://www.cbaleague.com/player/1",
        discovery_provider="search_engine",
        discovered_at="2026-09-18T00:00:00Z",
        title_hint="Synthetic",
    )
    manifest = collect_evidence_from_responses(
        [{
            "discovery": discovery,
            "status": 200,
            "content_type": "text/html",
            "body": (
                b'<span data-claim-type="OFFICIAL_PLAYER_NAME" '
                b'data-claim-value="Synthetic Player"></span>'
            ),
            "record_keys": ["r1"],
        }],
        output_root=tmp_path,
        extracted_at="2026-09-18T00:00:00Z",
        source_tier="A1",
        source_kind="registration_notice",
    )
    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["claims"][0]["normalized_value"] == (
        "Synthetic Player"
    )


def test_workbook_export_import_unicode_and_machine_tamper(tmp_path):
    packet = prepare_review_packet([
        proposal(
            "r1",
            "EXISTING_IDENTITY_CANDIDATE",
            "KEEP_UNDECIDED",
            candidate_player_uid="pid_0000000000000001",
            candidate_name_display_only="中文球员",
        )
    ])
    manifest = build_evidence_manifest([])
    workbook_path = tmp_path / "review_packet_workbook.xlsx"
    export_review_workbook(
        workbook_path,
        packet=packet,
        evidence_manifest=manifest,
        groups=[],
        batches=[],
    )
    workbook = load_workbook(workbook_path)
    assert workbook["Review"]["F2"].value == "中文球员"
    review_id = packet["reviews"][0]["review_id"]
    review_sheet = workbook["Review"]
    review_sheet["K2"] = "UNDECIDED"
    review_sheet["L2"] = "reviewed by synthetic human"
    review_sheet["P2"] = "2026-09-18T00:00:00Z"
    workbook.save(workbook_path)
    csv_bytes = import_review_workbook(
        workbook_path,
        packet=packet,
        evidence_manifest=manifest,
        groups=[],
        batches=[],
    )
    assert review_id.encode() in csv_bytes

    tampered = load_workbook(workbook_path)
    tampered["Review"]["B2"] = "tampered"
    tampered.save(workbook_path)
    with pytest.raises(IdentityWebEvidenceError, match="MACHINE_FIELD_TAMPER"):
        import_review_workbook(
            workbook_path,
            packet=packet,
            evidence_manifest=manifest,
            groups=[],
            batches=[],
        )
