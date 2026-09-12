"""Caller-owned source exclusions; Engine contains no real source IDs."""
import json
import os

SOURCE_POLICY_ENV = 'CBA_KB_SOURCE_POLICY_JSON'


def excluded_drive_ids(environ=None):
    env = os.environ if environ is None else environ
    raw = env.get(SOURCE_POLICY_ENV)
    if raw is None:
        raise RuntimeError('PRIVATE_SOURCE_POLICY_REQUIRED')
    try:
        policy = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError('PRIVATE_SOURCE_POLICY_INVALID') from exc
    values = policy.get('excluded_drive_ids') if isinstance(policy, dict) else None
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise RuntimeError('PRIVATE_SOURCE_POLICY_EXCLUSIONS_REQUIRED')
    return frozenset(values)


def excluded_from_current(source, *, excluded_ids=None):
    """Accept discovery items and registry rows, including source-ID-only rows."""
    excluded_ids = excluded_drive_ids() if excluded_ids is None else frozenset(excluded_ids)
    return any(str(source.get(key) or '').removeprefix('drive:') in excluded_ids
               for key in ('id', 'drive_file_id', 'source_id'))
