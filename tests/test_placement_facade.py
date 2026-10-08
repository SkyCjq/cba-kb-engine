"""F-01 bounded repair: placement enforcement facade tests.

Two layers:
1. Facade unit tests: forbidden parents rejected, good parents delegate,
   readback mismatch rejected, transparent delegation, audit trail.
2. Call-chain tests: prove the REAL creation chains (release.publish ->
   archive_snapshot, GoogleDriveStore.create -> handoff/provider canary)
   actually pass through enforcement. A silently-misplacing fake drive
   succeeds on old HEAD (no facade) and raises on the repaired HEAD --
   this is the falsification pair: these tests MUST fail on old HEAD.
"""

import os

import pytest

from cba_kb.placement import PlacementError

# test_release is importable: pytest inserts tests/ into sys.path
# (no __init__.py).
from test_release import FakeDrive as ReleaseFakeDrive, setup as release_setup


def _facade():
    """Lazy import: facade module does not exist on old HEAD, so the import
    must not be at module level (it would mask the behavioral falsification)."""
    from cba_kb.placement_facade import PlacementEnforcedDrive, placement_enforced

    return PlacementEnforcedDrive, placement_enforced


class FakeDrive:
    """Minimal Drive-like fake with faithful parent bookkeeping."""

    def __init__(self):
        self.files = {}
        self.seq = 0

    def _new_id(self):
        self.seq += 1
        return f"file-{self.seq}"

    def meta(self, file_id):
        return dict(self.files[file_id]["meta"])

    def get(self, file_id):
        return self.files[file_id]["data"]

    def list(self, parent):
        return [self.meta(fid) for fid, f in self.files.items() if parent in f["meta"]["parents"]]

    def ensure(self, parent, key, name, mime, content=None, *, index=None):
        fid = f"{parent}/{key}"
        if fid not in self.files:
            self.files[fid] = {
                "meta": {"id": fid, "name": name, "parents": [parent], "mimeType": mime, "version": "1"},
                "data": content or b"",
            }
        if index is not None:
            index[key] = self.meta(fid)
        return fid

    def ensure_copy(self, parent, key, file_id, name, *, index=None):
        fid = f"{parent}/{key}"
        if fid not in self.files:
            self.files[fid] = {
                "meta": {"id": fid, "name": name, "parents": [parent], "mimeType": "text/plain", "version": "1"},
                "data": b"",
            }
        if index is not None:
            index[key] = self.meta(fid)
        return fid


class MisplacingDrive(FakeDrive):
    """Silently misplaces: meta() reports a wrong parent. Old HEAD swallows this."""

    def meta(self, file_id):
        m = super().meta(file_id)
        m["parents"] = ["somewhere-else"]
        return m


class MisplacingReleaseDrive(ReleaseFakeDrive):
    """ReleaseFakeDrive variant whose readback lies about parents ONLY for
    files created in this session (surgical: pre-existing files stay truthful
    so preflight checks pass and the lie hits the facade readback)."""

    def __init__(self):
        super().__init__()
        self._misplaced = set()

    def ensure(self, parent, key, name, mime, content=None, *, index=None):
        fid = super().ensure(parent, key, name, mime, content, index=index)
        self._misplaced.add(fid)
        return fid

    def ensure_copy(self, parent, key, fid, name, *, index=None):
        new_id = super().ensure_copy(parent, key, fid, name, index=index)
        self._misplaced.add(new_id)
        return new_id

    def meta(self, fid):
        m = super().meta(fid)
        if fid in self._misplaced:
            m["parents"] = ["somewhere-else"]
        return m


# ----------------------------------------------------------------------
# 1. facade unit tests
# ----------------------------------------------------------------------
def test_forbidden_parents_rejected():
    PlacementEnforcedDrive, _ = _facade()
    facade = PlacementEnforcedDrive(FakeDrive())
    for bad in ("root", "my_drive", "cba_kb_root", "", None):
        with pytest.raises(PlacementError):
            facade.ensure(bad, "k", "n", "text/plain", b"x")
        with pytest.raises(PlacementError):
            facade.ensure_copy(bad, "k", "src-id", "n")


def test_good_parent_delegates_and_audits():
    PlacementEnforcedDrive, _ = _facade()
    raw = FakeDrive()
    facade = PlacementEnforcedDrive(raw)
    fid = facade.ensure("folder-1", "k1", "a.txt", "text/plain", b"hello")
    assert fid == "folder-1/k1"
    recs = facade.guarded_creations()
    assert len(recs) == 1
    assert recs[0].file_id == fid
    assert recs[0].parent_id == "folder-1"
    assert recs[0].operation == "ensure"
    fid2 = facade.ensure_copy("folder-1", "k2", fid, "b.txt")
    assert len(facade.guarded_creations()) == 2
    assert facade.guarded_creations()[1].operation == "ensure_copy"


def test_readback_mismatch_rejected():
    PlacementEnforcedDrive, _ = _facade()
    facade = PlacementEnforcedDrive(MisplacingDrive())
    with pytest.raises(PlacementError) as exc:
        facade.ensure("folder-1", "k1", "a.txt", "text/plain", b"hello")
    assert exc.value.code == "INVALID_PLACEMENT"
    assert facade.guarded_creations() == []


def test_transparent_delegation():
    PlacementEnforcedDrive, placement_enforced = _facade()
    raw = FakeDrive()
    facade = PlacementEnforcedDrive(raw)
    assert facade.list("folder-1") == raw.list("folder-1")
    assert placement_enforced(facade) is facade
    assert isinstance(placement_enforced(raw), PlacementEnforcedDrive)


# ----------------------------------------------------------------------
# 2. call-chain tests (falsification: MUST fail on old HEAD)
# ----------------------------------------------------------------------
def test_archive_snapshot_chain_enforced_and_audited(tmp_path):
    """archive_snapshot's ensure calls pass through the facade (audited)."""
    from cba_kb.release import archive_snapshot
    import json

    PlacementEnforcedDrive, _ = _facade()
    drive, root = release_setup(tmp_path)
    plan = json.loads((root / "plan.json").read_text())
    facade = PlacementEnforcedDrive(drive)
    previous = archive_snapshot(facade, root, plan)
    assert len(previous) == 2
    ops = [r.operation for r in facade.guarded_creations()]
    assert len(ops) >= 5  # release folder + before/candidate x2 + plan
    assert all(op == "ensure" for op in ops)


def test_archive_snapshot_chain_blocks_misplacement(tmp_path):
    """A silently-misplacing drive is caught INSIDE the real archive_snapshot chain."""
    from cba_kb.release import archive_snapshot
    import json

    drive, root = release_setup(tmp_path)
    plan = json.loads((root / "plan.json").read_text())
    PlacementEnforcedDrive, _ = _facade()
    bad = MisplacingReleaseDrive()
    bad.files = drive.files
    facade = PlacementEnforcedDrive(bad)
    with pytest.raises(PlacementError) as exc:
        archive_snapshot(facade, root, plan)
    assert exc.value.code == "INVALID_PLACEMENT"


def test_publish_wires_enforcement(tmp_path):
    """publish() itself wires the facade: misplacement raises even when the
    caller passes a raw drive. On old HEAD (no wiring) this test FAILS."""
    from cba_kb.release import publish

    drive, root = release_setup(tmp_path)
    bad = MisplacingReleaseDrive()
    bad.files = drive.files
    with pytest.raises(PlacementError) as exc:
        publish(bad, root, True)
    assert exc.value.code == "INVALID_PLACEMENT"


def test_store_create_chain_enforced():
    """GoogleDriveStore.create -> facade: misplacement is blocked and
    surfaces through the store's error wrapping."""
    from automation.drive_io import GoogleDriveStore
    from automation.models import P2AError

    PlacementEnforcedDrive, _ = _facade()
    store = GoogleDriveStore(PlacementEnforcedDrive(MisplacingDrive()))
    with pytest.raises(P2AError):
        store.create("folder-1", "history.md", b"data")


def test_store_create_chain_audited():
    """Happy path: store.create through the facade is audited."""
    from automation.drive_io import GoogleDriveStore

    PlacementEnforcedDrive, _ = _facade()
    raw = FakeDrive()
    facade = PlacementEnforcedDrive(raw)
    store = GoogleDriveStore(facade)
    result = store.create("folder-1", "history.md", b"data")
    assert result.content == b"data"
    assert len(facade.guarded_creations()) == 1
    assert facade.guarded_creations()[0].operation == "ensure"

# ----------------------------------------------------------------------
# 3. round-2: residual-root counterexamples (exact set equality, D1)
#
# Independent Q2 (new HEAD) upheld F-01: membership check accepted
# ["folder-1", "root"]. The facade now requires exact set equality.
# ----------------------------------------------------------------------

class ResidualRootDrive(FakeDrive):
    """New files read back with a residual root parent."""

    def __init__(self):
        super().__init__()
        self._created = set()

    def ensure(self, parent, key, name, mime, content=None, *, index=None):
        fid = super().ensure(parent, key, name, mime, content, index=index)
        self._created.add(fid)
        return fid

    def ensure_copy(self, parent, key, file_id, name, *, index=None):
        fid = super().ensure_copy(parent, key, file_id, name, index=index)
        self._created.add(fid)
        return fid

    def meta(self, file_id):
        m = super().meta(file_id)
        if file_id in self._created:
            m["parents"] = m["parents"] + ["root"]
        return m


def test_ensure_residual_root_rejected():
    PlacementEnforcedDrive, _ = _facade()
    facade = PlacementEnforcedDrive(ResidualRootDrive())
    with pytest.raises(PlacementError) as exc:
        facade.ensure("folder-1", "k1", "a.txt", "text/plain", b"x")
    assert exc.value.code == "INVALID_PLACEMENT"
    assert facade.guarded_creations() == []


def test_ensure_copy_residual_root_rejected():
    PlacementEnforcedDrive, _ = _facade()
    raw = ResidualRootDrive()
    facade = PlacementEnforcedDrive(raw)
    src = raw.ensure("folder-1", "src", "s.txt", "text/plain", b"x")
    with pytest.raises(PlacementError) as exc:
        facade.ensure_copy("folder-1", "k2", src, "b.txt")
    assert exc.value.code == "INVALID_PLACEMENT"
    assert facade.guarded_creations() == []


def test_publish_chain_residual_root_rejected(tmp_path):
    """Residual root through the REAL publish chain is blocked (entry-chain
    behavioral test, not just facade unit test)."""
    from cba_kb.release import publish

    drive, root = release_setup(tmp_path)

    class ResidualRootReleaseDrive(ReleaseFakeDrive):
        def __init__(self):
            super().__init__()
            self._created = set()

        def ensure(self, parent, key, name, mime, content=None, *, index=None):
            fid = super().ensure(parent, key, name, mime, content, index=index)
            self._created.add(fid)
            return fid

        def ensure_copy(self, parent, key, fid, name, *, index=None):
            new_id = super().ensure_copy(parent, key, fid, name, index=index)
            self._created.add(new_id)
            return new_id

        def meta(self, fid):
            m = super().meta(fid)
            if fid in self._created:
                m["parents"] = m["parents"] + ["root"]
            return m

    bad = ResidualRootReleaseDrive()
    bad.files = drive.files
    with pytest.raises(PlacementError) as exc:
        publish(bad, root, True)
    assert exc.value.code == "INVALID_PLACEMENT"


# ----------------------------------------------------------------------
# 4. entry-point wiring regression tests (reviewer recommendation)
# ----------------------------------------------------------------------

def test_from_trusted_runtime_wires_facade():
    """Regression: from_trusted_runtime must hand the store a
    PlacementEnforcedDrive, so the wiring cannot be silently removed."""
    from unittest import mock
    from automation.drive_io import GoogleDriveStore
    PlacementEnforcedDrive, _ = _facade()

    with mock.patch("cba_kb.drive.Drive") as MockDrive, \
         mock.patch("cba_kb.instance.load_instance"):
        store = GoogleDriveStore.from_trusted_runtime("/engine", "/instance")
    assert isinstance(store.drive, PlacementEnforcedDrive)
    assert MockDrive.called


def test_reserve_staging_dispatch_wires_facade(tmp_path):
    """Regression: the reserve-staging CLI dispatch must hand reserve_staging
    a PlacementEnforcedDrive, so the wiring cannot be silently removed."""
    import importlib.util
    from unittest import mock
    PlacementEnforcedDrive, _ = _facade()

    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(here, "..", "scripts", "prepare_production.py")
    spec = importlib.util.spec_from_file_location("prepare_production", script)
    pp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pp)

    captured = {}

    def fake_reserve_staging(drive, *a, **k):
        captured["drive"] = drive
        return {"ok": True}

    proj = tmp_path / "projection.json"
    proj.write_text("{}")

    with mock.patch.object(pp, "load_instance"), \
         mock.patch.object(pp, "_private_output"), \
         mock.patch.object(pp, "read", return_value={}), \
         mock.patch.object(pp, "Drive"), \
         mock.patch.object(pp, "reserve_staging", side_effect=fake_reserve_staging), \
         mock.patch.object(pp, "_require_release"):
        pp.main(["reserve-staging",
                 "--instance-root", str(tmp_path),
                 "--output", str(tmp_path / "out"),
                 "--projection", str(proj)])
    assert isinstance(captured["drive"], PlacementEnforcedDrive)
