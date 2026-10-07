import pytest
from cba_kb.placement import (
    PlacementGuard,
    PlacementRegistry,
    PlacementError,
    PlacementStatus,
    ArtifactPlacementRequest,
    audit_active_placements,
)


class FakeDriveClient:
    def __init__(self):
        self.files = {}
        self.counter = 0

    def create(self, name, parent=None, mime="text/plain", appProperties=None):
        self.counter += 1
        fid = f"file-{self.counter}"
        parents = [parent] if parent else []
        self.files[fid] = {
            "id": fid,
            "name": name,
            "parents": list(parents),
            "mimeType": mime,
            "appProperties": appProperties or {},
        }
        return {"id": fid}

    def meta(self, file_id):
        if file_id not in self.files:
            raise KeyError(f"File {file_id} not found")
        return dict(self.files[file_id])

    def reparent(self, file_id, new_parent, old_parent=None):
        if file_id not in self.files:
            raise KeyError(f"File {file_id} not found")
        cur_parents = list(self.files[file_id]["parents"])
        if old_parent and old_parent in cur_parents:
            cur_parents.remove(old_parent)
        if new_parent not in cur_parents:
            cur_parents.append(new_parent)
        self.files[file_id]["parents"] = cur_parents
        return dict(self.files[file_id])

    def list(self, parent):
        return [dict(f) for f in self.files.values() if parent in f.get("parents", [])]

    def delete(self, file_id):
        if file_id in self.files:
            del self.files[file_id]


def build_standard_registry():
    folder_map = {
        "INBOX": "folder-inbox-00",
        "SOURCES": "folder-sources-10",
        "SOURCE_DERIVED": "folder-sources-10",
        "DATA": "folder-data-20",
        "NOTES": "folder-notes-30",
        "AI_VIEW": "folder-ai-40",
        "CONFIG": "folder-config-60",
        "GOVERNANCE_CURRENT": "folder-gov-current",
        "GOVERNANCE_HISTORY": "folder-gov-history",
        "GOVERNANCE_EVIDENCE": "folder-gov-evidence",
        "GOVERNANCE_STAGING": "folder-gov-staging",
        "STAGING_ARCHIVE": "folder-archive-90",
        "VERSION_DEV": "folder-v202-dev",
    }
    return PlacementRegistry(
        folder_map=folder_map,
        disallowed_roots={"root", "my_drive", "cba_kb_root"},
        allowed_override_parents={"folder-allowed-override"},
    )


# ==================== 原有 7 个 probe 测试（断言不减） ====================

# 1. test_unmapped_artifact_fail_closed
def test_unmapped_artifact_fail_closed():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="UNKNOWN_CLASS",
        artifact_role="UNKNOWN_ROLE",
        name="unknown.txt",
    )
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "UNMAPPED_ARTIFACT"
    assert "UNKNOWN_CLASS" in str(exc_info.value)


# 2. test_root_parent_rejected
def test_root_parent_rejected():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    # Attempt to resolve or enforce placement to a forbidden root
    with pytest.raises(PlacementError) as exc_info:
        registry.validate_parent("root")
    assert exc_info.value.code == "ROOT_PLACEMENT_FORBIDDEN"

    with pytest.raises(PlacementError) as exc_info2:
        registry.validate_parent("cba_kb_root")
    assert exc_info2.value.code == "ROOT_PLACEMENT_FORBIDDEN"


# 3. test_reparent_atomicity
def test_reparent_atomicity():
    registry = build_standard_registry()
    drive = FakeDriveClient()

    class FailingReparentDrive(FakeDriveClient):
        def reparent(self, file_id, new_parent, old_parent=None):
            # Simulate failure during reparent step
            raise RuntimeError("Drive API timeout during reparent")

    failing_drive = FailingReparentDrive()
    guard = PlacementGuard(drive=failing_drive, registry=registry)

    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="DATA",
        artifact_role="CANONICAL",
        name="facts.json",
        must_reparent=True,  # simulate legacy root-creation requirement
    )

    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "REPARENT_FAILED"
    # Verify uncommitted state & blocking dispatch
    uncommitted = guard.get_uncommitted()
    assert len(uncommitted) == 1
    assert uncommitted[0].status == PlacementStatus.UNCOMMITTED
    with pytest.raises(PlacementError) as dispatch_exc:
        guard.assert_safe_to_dispatch(uncommitted[0].file_id)
    assert dispatch_exc.value.code == "UNCOMMITTED_PLACEMENT"


# 4. test_history_vs_active
def test_history_vs_active():
    registry = PlacementRegistry(
        folder_map={
            "INBOX": "folder-inbox-00",
            "GOVERNANCE_CURRENT": "folder-gov-current",
            "GOVERNANCE_HISTORY": "folder-gov-history",
            "GOVERNANCE_STAGING": "folder-gov-staging",
        },
        disallowed_roots={"root", "my_drive", "cba_kb_root"},
        allowed_override_parents={"folder-gov-current"},
    )
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    # HISTORY artifact routed to ACTIVE directory must raise INVALID_PLACEMENT
    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="HISTORY",
        artifact_role="AUDIT_LOG",
        name="history.log",
        override_parent_id="folder-gov-current",  # Force ACTIVE directory
    )
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "INVALID_PLACEMENT"


# 5. test_retry_idempotent
def test_retry_idempotent():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="NOTES",
        artifact_role="MANUAL",
        name="notes_2026.md",
        idempotency_key="idemp-notes-001",
    )

    res1 = guard.create_artifact(req)
    assert res1.status == PlacementStatus.COMMITTED

    # Retry same request with same idempotency key
    res2 = guard.create_artifact(req)
    assert res2.status == PlacementStatus.COMMITTED
    assert res2.file_id == res1.file_id
    assert len(drive.files) == 1


# 6. test_wrong_parent_readback
def test_wrong_parent_readback():
    registry = build_standard_registry()
    drive = FakeDriveClient()

    class CorruptingDrive(FakeDriveClient):
        def meta(self, file_id):
            item = dict(super().meta(file_id))
            # Simulate Drive metadata readback pointing to wrong parent
            item["parents"] = ["wrong-folder-99"]
            return item

    corrupting_drive = CorruptingDrive()
    guard = PlacementGuard(drive=corrupting_drive, registry=registry)

    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="AI_VIEW",
        artifact_role="INDEX",
        name="ai_index.json",
    )
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "INVALID_PLACEMENT"
    assert "Readback parents set" in str(exc_info.value)


# 7. test_registry_lookup
def test_registry_lookup():
    registry = build_standard_registry()

    expected_lookups = [
        ("INBOX", "BATCH", "folder-inbox-00"),
        ("SOURCE", "ORIGINAL", "folder-sources-10"),
        ("SOURCE_DERIVED", "OCR", "folder-sources-10"),
        ("DATA", "CANONICAL", "folder-data-20"),
        ("NOTES", "MANUAL", "folder-notes-30"),
        ("AI_VIEW", "INDEX", "folder-ai-40"),
        ("CONFIG", "REGISTRY", "folder-config-60"),
        ("CANONICAL", "GOVERNANCE", "folder-gov-current/REQ-202-TEST"),
        ("ACTIVE_WORK", "STAGING", "folder-gov-staging/REQ-202-TEST"),
        ("HISTORY", "ARCHIVE", "folder-gov-history/REQ-202-TEST"),
        ("EVIDENCE", "RAW", "folder-gov-evidence/REQ-202-TEST"),
        ("VERSION_DEV", "DOC", "folder-v202-dev"),
    ]

    for artifact_class, artifact_role, expected_parent in expected_lookups:
        parent_id = registry.resolve_parent_id(
            req_id="REQ-202-TEST",
            artifact_class=artifact_class,
            artifact_role=artifact_role,
        )
        assert parent_id == expected_parent, f"Failed for {artifact_class}/{artifact_role}"


# ==================== D1-D6 新增探针测试 ====================

# D1 Probe: test_exact_readback_rejects_residual_root
def test_exact_readback_rejects_residual_root():
    registry = build_standard_registry()
    drive = FakeDriveClient()

    class ResidualRootDrive(FakeDriveClient):
        def meta(self, file_id):
            item = dict(super().meta(file_id))
            target = item["parents"][0] if item["parents"] else "unknown"
            # Simulate parents containing BOTH expected parent and residual root
            item["parents"] = [target, "root"]
            return item

    residual_drive = ResidualRootDrive()
    guard = PlacementGuard(drive=residual_drive, registry=registry)

    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="DATA",
        artifact_role="CANONICAL",
        name="facts.json",
    )
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "INVALID_PLACEMENT"
    # Must fail closed on dispatch
    with pytest.raises(PlacementError) as dispatch_exc:
        guard.assert_safe_to_dispatch("file-1")
    assert dispatch_exc.value.code == "UNCOMMITTED_PLACEMENT"


# D2 Probe: test_history_cannot_enter_staging_and_class_semantics
def test_history_cannot_enter_staging_and_class_semantics():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    # HISTORY class cannot be directed to STAGING folder
    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="HISTORY",
        artifact_role="AUDIT_LOG",
        name="history_item.json",
        override_parent_id="folder-gov-staging",
    )
    # Even if override is in allowed list, class semantics must reject
    registry.allowed_override_parents.add("folder-gov-staging")
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "INVALID_PLACEMENT"
    assert "HISTORY class artifact" in str(exc_info.value)

    # Non-staging artifact class cannot enter STAGING folder
    req2 = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="DATA",
        artifact_role="CANONICAL",
        name="data.json",
        override_parent_id="folder-gov-staging",
    )
    with pytest.raises(PlacementError) as exc_info2:
        guard.create_artifact(req2)
    assert exc_info2.value.code == "INVALID_PLACEMENT"
    assert "Non-staging artifact class" in str(exc_info2.value)


# D3 Probe: test_durable_idempotency_across_two_guard_instances
def test_durable_idempotency_across_two_guard_instances():
    registry = build_standard_registry()
    shared_drive = FakeDriveClient()

    # Guard instance 1 creates artifact
    guard1 = PlacementGuard(drive=shared_drive, registry=registry)
    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="NOTES",
        artifact_role="MANUAL",
        name="notes_doc.md",
        idempotency_key="durable-key-abc-123",
    )
    rec1 = guard1.create_artifact(req)
    assert rec1.status == PlacementStatus.COMMITTED
    file_id_1 = rec1.file_id

    # Guard instance 2 (independent instance with empty in-memory cache)
    guard2 = PlacementGuard(drive=shared_drive, registry=registry)
    assert len(guard2._records) == 0

    # Retry same request on instance 2
    rec2 = guard2.create_artifact(req)
    assert rec2.status == PlacementStatus.COMMITTED
    assert rec2.file_id == file_id_1
    assert len(shared_drive.files) == 1, "Must not create a second file in Drive"


# D4 Probe: test_dynamic_resolution_with_real_parameters
def test_dynamic_resolution_with_real_parameters():
    registry = build_standard_registry()

    # 1. INBOX with batch_id
    parent_inbox_batch = registry.resolve_parent_id(
        req_id="REQ-202-TEST",
        artifact_class="INBOX",
        artifact_role="BATCH",
        batch_id="20261007_B01",
    )
    assert parent_inbox_batch == "folder-inbox-00/20261007_B01"

    # 2. SOURCE_DERIVED with source_pdf_parent_id
    parent_ocr = registry.resolve_parent_id(
        req_id="REQ-202-TEST",
        artifact_class="SOURCE_DERIVED",
        artifact_role="OCR",
        source_pdf_parent_id="folder-sources-sub-123",
    )
    assert parent_ocr == "folder-sources-sub-123"

    # 3. Governance artifacts with dynamic REQ-ID
    parent_gov_staging = registry.resolve_parent_id(
        req_id="REQ-190-DATA-CORPUS",
        artifact_class="ACTIVE_WORK",
        artifact_role="STAGING",
    )
    assert parent_gov_staging == "folder-gov-staging/REQ-190-DATA-CORPUS"

    parent_gov_archive = registry.resolve_parent_id(
        req_id="REQ-190-DATA-CORPUS",
        artifact_class="STAGING_ARCHIVE",
        artifact_role="ARCHIVE",
    )
    assert parent_gov_archive == "folder-archive-90/REQ-190-DATA-CORPUS"


# D5 Probe: test_unallowed_override_parent_rejected
def test_unallowed_override_parent_rejected():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    # Attempt to override to a folder not in allowed_override_parents
    req = ArtifactPlacementRequest(
        req_id="REQ-202-TEST",
        artifact_class="DATA",
        artifact_role="CANONICAL",
        name="arbitrary.json",
        override_parent_id="folder-unauthorized-random",
    )
    with pytest.raises(PlacementError) as exc_info:
        guard.create_artifact(req)
    assert exc_info.value.code == "OVERRIDE_NOT_ALLOWED"


# D6 Probe: test_unknown_file_id_dispatch_rejected
def test_unknown_file_id_dispatch_rejected():
    registry = build_standard_registry()
    drive = FakeDriveClient()
    guard = PlacementGuard(drive=drive, registry=registry)

    with pytest.raises(PlacementError) as exc_info:
        guard.assert_safe_to_dispatch("unknown-file-999")
    assert exc_info.value.code == "UNKNOWN_FILE_DISPATCH"
