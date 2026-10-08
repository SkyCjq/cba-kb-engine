from pathlib import Path

import pytest

from scripts.prepare_v1_5_2 import (
    DOMAIN_ROW_TOTAL,
    DOMAIN_WORKBOOK,
    merged_registry,
    validate_domain_candidate,
)


ROOT = Path(__file__).resolve().parents[1]


def test_v1_5_2_registry_proposal_is_public_safe():
    header = (
        'source_id,drive_file_id,source_title,source_url,current_parent_id,'
        'original_discovery_path,source_type,source_role,season,club_id,'
        'content_hash,file_size_bytes,business_status,extraction_status,'
        'extraction_method,validation_status,records_generated,records_imported,'
        'discovered_at,registered_at,processed_at,verified_at,notes\n'
    )
    content, added = merged_registry(
        ROOT,
        header.encode('utf-8-sig'),
        excluded_ids=(),
    )
    assert added == []
    assert list(content.decode('utf-8-sig').splitlines()) == [header.strip()]


@pytest.mark.skipif(
    not DOMAIN_WORKBOOK.exists(),
    reason='real DRAFT-2 candidate is intentionally not in Git',
)
def test_v1_5_2_domain_candidate_is_pinned():
    workbook, acceptance = validate_domain_candidate()
    assert workbook
    assert DOMAIN_ROW_TOTAL == 600
    assert acceptance['workbook_sha256'] == (
        '4124c4e6f8e3f62d7353180a5bbd4baca51e3196e2b7fac6b06148042fce2849'
    )
