"""Generic boundary for caller-owned CBA-KB instance configuration.

The Engine never contains a default instance path or real target identifiers.
An operator must explicitly supply an instance root (or set
``CBA_KB_INSTANCE_ROOT``); missing private configuration fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path


INSTANCE_ROOT_ENV = "CBA_KB_INSTANCE_ROOT"
CONFIG_FILES = frozenset({
    "runtime.json", "production.json", "sandbox.json", "drive_map.yaml",
    "import_inventory.json", "source_policy.json",
    "v1.5.2_registry_proposal.json", "v1.5.2_registry_status.json",
})


def _resolved(path):
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class Instance:
    root: Path
    config_root: Path
    credentials_store: Path
    real_fixture_root: Path
    data_root: Path

    @property
    def document_input_root(self):
        return self.root / "inbox" / "documents"

    @property
    def document_archive_root(self):
        return self.data_root / "document_lane" / "archive"

    @property
    def document_review_root(self):
        return self.data_root / "document_lane" / "review"

    @property
    def document_report_root(self):
        return self.data_root / "document_lane" / "reports"

    def config_path(self, name):
        if name not in CONFIG_FILES:
            raise ValueError(f"UNKNOWN_INSTANCE_CONFIG: {name}")
        path = (self.config_root / name).resolve()
        if path.parent != self.config_root:
            raise ValueError("INSTANCE_CONFIG_PATH_ESCAPE")
        return path

    def read_json(self, name):
        path = self.config_path(name)
        if not path.is_file():
            raise RuntimeError(f"PRIVATE_INSTANCE_CONFIG_REQUIRED: {name}")
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"PRIVATE_INSTANCE_CONFIG_INVALID: {name}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"PRIVATE_INSTANCE_CONFIG_OBJECT_REQUIRED: {name}")
        return value


def load_instance(engine_root, instance_root=None, *, environ=None):
    """Resolve a private instance without embedding or guessing its location."""
    env = os.environ if environ is None else environ
    supplied = instance_root or env.get(INSTANCE_ROOT_ENV)
    if not supplied:
        raise RuntimeError(
            f"PRIVATE_INSTANCE_REQUIRED: pass --instance-root or set {INSTANCE_ROOT_ENV}"
        )
    engine = _resolved(engine_root)
    root = _resolved(supplied)
    try:
        root.relative_to(engine)
    except ValueError:
        pass
    else:
        raise RuntimeError("PRIVATE_INSTANCE_MUST_BE_OUTSIDE_ENGINE")
    try:
        engine.relative_to(root)
    except ValueError:
        pass
    else:
        raise RuntimeError("ENGINE_MUST_BE_OUTSIDE_PRIVATE_INSTANCE")
    if not root.is_dir():
        raise RuntimeError("PRIVATE_INSTANCE_ROOT_MISSING")
    config_root = (root / "config").resolve()
    if config_root.parent != root:
        raise RuntimeError("PRIVATE_INSTANCE_CONFIG_MUST_STAY_INSIDE_INSTANCE")
    if not config_root.is_dir():
        raise RuntimeError("PRIVATE_INSTANCE_CONFIG_ROOT_MISSING")
    return Instance(
        root=root,
        config_root=config_root,
        credentials_store=(root / ".credentials").resolve(),
        real_fixture_root=(root / "fixtures" / "real").resolve(),
        data_root=(root / "data").resolve(),
    )


def require_mapping(mapping, fields, *, label):
    """Validate private target mappings before any transport is constructed."""
    if not isinstance(mapping, dict):
        raise RuntimeError(f"PRIVATE_MAPPING_OBJECT_REQUIRED: {label}")
    missing = sorted(
        field for field in fields
        if not mapping.get(field)
        or (isinstance(mapping.get(field), str)
            and mapping[field].startswith("<") and mapping[field].endswith(">"))
    )
    if missing:
        raise RuntimeError(f"PRIVATE_MAPPING_REQUIRED: {label}: {','.join(missing)}")
    return mapping
