"""Official web evidence enrichment and scalable human review support.

This module never creates final identity authority. It produces candidate-only
evidence metadata, explicit human-authority expansion, and non-canonical XLSX
working projections.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Protection

from .evidence_ledger import canonical_bytes
from .identity_coverage import (
    HUMAN_REVIEW_FIELDS,
    MACHINE_REVIEW_FIELDS,
    REVIEW_FIELDS,
    validate_review_packet,
)
from .player_identity import validate_player_uid


SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "identity-web-evidence-v1"
EVIDENCE_CLASSES = frozenset({
    "W1_OFFICIAL_PERSON_ID_EXACT",
    "W2_OFFICIAL_BIO_MULTI_SOURCE",
    "W3_OFFICIAL_CONTINUITY",
    "W4_DISCOVERY_SUPPORT",
    "WX_CONFLICT",
})
BATCH_PREDICATES = frozenset({
    "BATCH_EXISTING_W1",
    "BATCH_EXISTING_W2",
    "BATCH_NO_SAFE_SEARCH_EXHAUSTED",
})
WORKBOOK_SHEETS = (
    "Review",
    "Groups",
    "Batches",
    "Evidence_Index",
    "Instructions",
)
INSTRUCTIONS_ROWS = (
    ("Authority boundary",),
    ("Workbook is non-canonical human workflow projection only.",),
    ("Only human-owned cells may be edited.",),
    ("Do not paste source bodies into this workbook.",),
)
SOURCE_TIERS = {
    "A0": frozenset({
        "cba.net.cn",
        "www.cba.net.cn",
        "cbanetcdn.cba.net.cn",
    }),
    "A1": frozenset({
        "cbaleague.com",
        "www.cbaleague.com",
        "image.cbaleague.com",
    }),
    "A2": frozenset({
        "cbaleague.com",
        "www.cbaleague.com",
    }),
}
DISCOVERY_PROVIDERS = frozenset({
    "search_engine",
    "mainstream_media",
    "encyclopedia",
    "forum",
    "social_post",
    "search_snippet",
    "agent",
})
CLAIM_TYPES = frozenset({
    "OFFICIAL_PLAYER_NAME",
    "OFFICIAL_BIRTH_DATE",
    "OFFICIAL_REGISTRATION_UNIT",
    "OFFICIAL_SEASON",
    "OFFICIAL_TEAM",
    "OFFICIAL_JERSEY_NUMBER",
    "OFFICIAL_SOURCE_DECLARED_PERSON_ID",
    "OFFICIAL_TRANSACTION_OR_REGISTRATION_EVENT",
})
EXTERNAL_ID_NAMESPACE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_SUSPICIOUS_TEXT = re.compile(r"[�]|Ã|Â|锟|拷|娌|鎹")
_DOMAIN_LAST_REQUEST = {}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class IdentityWebEvidenceError(RuntimeError):
    pass


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise IdentityWebEvidenceError(f"{label}_REQUIRED")
    return value.strip()


def _required_sha256(value, label):
    value = _required_text(value, label)
    if not _SHA256.fullmatch(value):
        raise IdentityWebEvidenceError(f"{label}_INVALID")
    return value


def _normalize_text(value):
    text = unicodedata.normalize("NFKC", _required_text(value, "VALUE"))
    return " ".join(text.strip().split())


def source_domain(url):
    parsed = urlparse(_required_text(url, "SOURCE_URL"))
    if parsed.scheme not in {"http", "https"}:
        raise IdentityWebEvidenceError("PUBLIC_HTTP_URL_REQUIRED")
    if parsed.username or parsed.password:
        raise IdentityWebEvidenceError("URL_CREDENTIALS_FORBIDDEN")
    if not parsed.hostname:
        raise IdentityWebEvidenceError("SOURCE_DOMAIN_REQUIRED")
    return parsed.hostname.lower()


def validate_source_url(
    url,
    *,
    source_tier,
    private_b0_domains=None,
):
    domain = source_domain(url)
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(token in path for token in (
        "/login",
        "/signin",
        "/oauth",
        "/private-api",
        "/api/private",
    )):
        raise IdentityWebEvidenceError("AUTHENTICATED_RESOURCE_FORBIDDEN")
    if source_tier in SOURCE_TIERS:
        if domain not in SOURCE_TIERS[source_tier]:
            raise IdentityWebEvidenceError("SOURCE_DOMAIN_NOT_ALLOWED")
    elif source_tier == "B0":
        approved = {
            value.lower()
            for value in (private_b0_domains or [])
        }
        if domain not in approved:
            raise IdentityWebEvidenceError("B0_DOMAIN_NOT_APPROVED")
    elif source_tier == "D0":
        raise IdentityWebEvidenceError("DISCOVERY_SOURCE_NOT_AUTHORITY")
    else:
        raise IdentityWebEvidenceError("SOURCE_TIER_INVALID")
    return {
        "url": url,
        "domain": domain,
        "source_tier": source_tier,
    }


def validate_redirect(
    original_url,
    destination_url,
    *,
    source_tier,
    private_b0_domains=None,
):
    validate_source_url(
        original_url,
        source_tier=source_tier,
        private_b0_domains=private_b0_domains,
    )
    return validate_source_url(
        destination_url,
        source_tier=source_tier,
        private_b0_domains=private_b0_domains,
    )


def validate_fetch_request(url, *, method="GET", headers=None):
    if method not in {"GET", "HEAD"}:
        raise IdentityWebEvidenceError("PUBLIC_FETCH_METHOD_INVALID")
    headers = headers or {}
    if any(str(key).lower() in {"authorization", "cookie"} for key in headers):
        raise IdentityWebEvidenceError("AUTHENTICATED_HEADERS_FORBIDDEN")
    parsed = urlparse(_required_text(url, "SOURCE_URL"))
    query = parsed.query.lower()
    blocked_query_keys = ("token", "session", "auth", "cookie")
    if any(f"{token}=" in query for token in blocked_query_keys):
        raise IdentityWebEvidenceError("AUTHENTICATED_QUERY_FORBIDDEN")
    source_domain(url)
    return {"url": url, "method": method, "headers": {}}


def build_discovery_record(
    *,
    query,
    discovered_url,
    discovery_provider,
    discovered_at,
    title_hint=None,
):
    if discovery_provider not in DISCOVERY_PROVIDERS:
        raise IdentityWebEvidenceError("DISCOVERY_PROVIDER_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "authority": "DISCOVERY_ONLY",
        "query": _required_text(query, "QUERY"),
        "discovered_url": discovered_url,
        "discovered_domain": source_domain(discovered_url),
        "discovered_at": _required_text(discovered_at, "DISCOVERED_AT"),
        "discovery_provider": discovery_provider,
        "title_hint": title_hint,
    }


def validate_discovery_record(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("DISCOVERY_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "authority",
        "query",
        "discovered_url",
        "discovered_domain",
        "discovered_at",
        "discovery_provider",
        "title_hint",
    }
    if set(value) != required:
        raise IdentityWebEvidenceError("DISCOVERY_SCHEMA_INVALID")
    if value["authority"] != "DISCOVERY_ONLY":
        raise IdentityWebEvidenceError("DISCOVERY_AUTHORITY_INVALID")
    if value["discovery_provider"] not in DISCOVERY_PROVIDERS:
        raise IdentityWebEvidenceError("DISCOVERY_PROVIDER_INVALID")
    if source_domain(value["discovered_url"]) != value["discovered_domain"]:
        raise IdentityWebEvidenceError("DISCOVERY_DOMAIN_MISMATCH")
    return value


def collect_evidence_from_responses(
    responses,
    *,
    output_root,
    extracted_at,
    source_tier,
    source_kind,
    extractor_version=EXTRACTOR_VERSION,
    max_bytes=10_000_000,
    timeout_seconds=10,
    private_b0_domains=None,
):
    output_root = Path(output_root)
    raw_root = output_root / "raw"
    raw_root.mkdir(parents=True, exist_ok=True)
    items = []
    for response in responses:
        discovery = validate_discovery_record(response["discovery"])
        body = response["body"]
        if not isinstance(body, bytes):
            raise IdentityWebEvidenceError("RESPONSE_BODY_BYTES_REQUIRED")
        validate_response_guard(
            status=response["status"],
            content_type=response["content_type"],
            content=body,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
        validate_source_url(
            discovery["discovered_url"],
            source_tier=source_tier,
            private_b0_domains=private_b0_domains,
        )
        claims = response.get("claims")
        if claims is None:
            if response["content_type"].startswith("text/html"):
                claims = extract_html_claims(body)
            elif response["content_type"] == "application/pdf":
                claims = extract_pdf_claims(
                    body,
                    text_extractor=response.get("text_extractor"),
                )
            else:
                claims = []
        snapshot_name = content_sha256(body) + ".bin"
        snapshot_path = raw_root / snapshot_name
        if snapshot_path.exists():
            if snapshot_path.read_bytes() != body:
                raise IdentityWebEvidenceError("RAW_SNAPSHOT_CONFLICT")
        else:
            snapshot_path.write_bytes(body)
        items.append(build_evidence_item(
            source_tier=source_tier,
            source_kind=source_kind,
            source_url=discovery["discovered_url"],
            fetched_at=extracted_at,
            http_status=response["status"],
            content_type=response["content_type"],
            content=body,
            raw_snapshot_path=str(snapshot_path),
            claims=claims,
            record_keys=response.get("record_keys", []),
            published_at=response.get("published_at"),
            extraction_warnings=response.get("extraction_warnings"),
            extractor_version=extractor_version,
            private_b0_domains=private_b0_domains,
        ))
    return build_evidence_manifest(items)


def validate_response_guard(
    *,
    status,
    content_type,
    content,
    max_bytes,
    timeout_seconds,
):
    if timeout_seconds <= 0:
        raise IdentityWebEvidenceError("FETCH_TIMEOUT_REQUIRED")
    if status < 200 or status >= 300:
        raise IdentityWebEvidenceError("FETCH_STATUS_FAILED")
    if len(content) > max_bytes:
        raise IdentityWebEvidenceError("FETCH_RESPONSE_TOO_LARGE")
    if not content_type:
        raise IdentityWebEvidenceError("CONTENT_TYPE_REQUIRED")
    return {
        "status": status,
        "content_type": content_type,
        "bytes": len(content),
    }


def _urllib_transport(url, *, method, timeout, headers, max_bytes=10_000_000):
    request = urllib.request.Request(url, method=method, headers=headers)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return {
                "status": response.status,
                "content_type": response.headers.get_content_type(),
                "body": response.read(max_bytes + 1),
                "location": response.headers.get("Location"),
                "redirects": [],
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "content_type": exc.headers.get_content_type(),
            "body": exc.read(max_bytes + 1),
            "location": exc.headers.get("Location"),
            "redirects": [],
        }


def fetch_official_resource(
    url,
    *,
    source_tier,
    method="GET",
    headers=None,
    timeout_seconds=10,
    max_bytes=10_000_000,
    max_retries=2,
    max_redirects=5,
    per_domain_interval=1.0,
    transport=None,
    sleep=time.sleep,
    clock=time.monotonic,
    private_b0_domains=None,
):
    request = validate_fetch_request(url, method=method, headers=headers)
    validate_source_url(
        url,
        source_tier=source_tier,
        private_b0_domains=private_b0_domains,
    )
    domain = source_domain(url)
    transport = transport or _urllib_transport
    def throttle(url_value):
        current_domain = source_domain(url_value)
        last_request = _DOMAIN_LAST_REQUEST.get(current_domain)
        current = clock()
        if (
            last_request is not None
            and current - last_request < per_domain_interval
        ):
            sleep(per_domain_interval - (current - last_request))
        _DOMAIN_LAST_REQUEST[current_domain] = clock()

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            current_url = url
            redirect_count = 0
            redirect_chain = []
            while True:
                throttle(current_url)
                try:
                    response = transport(
                        current_url,
                        method=request["method"],
                        timeout=timeout_seconds,
                        headers=request["headers"],
                        max_bytes=max_bytes,
                    )
                except TypeError:
                    response = transport(
                        current_url,
                        method=request["method"],
                        timeout=timeout_seconds,
                        headers=request["headers"],
                    )
                redirects = list(response.get("redirects", []))
                if response.get("location"):
                    redirects.append(response["location"])
                if redirects:
                    for redirect in redirects:
                        validate_redirect(
                            current_url,
                            redirect,
                            source_tier=source_tier,
                            private_b0_domains=private_b0_domains,
                        )
                    redirect_count += len(redirects)
                    if redirect_count > max_redirects:
                        raise IdentityWebEvidenceError(
                            "REDIRECT_LIMIT_EXCEEDED",
                        )
                    current_url = redirects[-1]
                    redirect_chain.extend(redirects)
                    continue
                break
            validate_response_guard(
                status=response["status"],
                content_type=response["content_type"],
                content=response["body"],
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )
            return {
                "url": current_url,
                "domain": source_domain(current_url),
                "status": response["status"],
                "content_type": response["content_type"],
                "content": response["body"],
                "redirect_chain": redirect_chain,
                "attempts": attempt + 1,
            }
        except IdentityWebEvidenceError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < max_retries:
                sleep(per_domain_interval)
    raise IdentityWebEvidenceError("PUBLIC_FETCH_FAILED") from last_error


def build_fetch_provenance_authority(entries):
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise IdentityWebEvidenceError("FETCH_PROVENANCE_ENTRY_INVALID")
        required = {
            "discovery_id",
            "context_reference",
            "original_discovered_url",
            "redirect_chain",
            "final_source_url",
            "evidence_id",
            "content_sha256",
            "source_tier",
            "source_kind",
        }
        if set(entry) != required:
            raise IdentityWebEvidenceError("FETCH_PROVENANCE_ENTRY_INVALID")
        core = {
            **entry,
            "redirect_chain": list(entry["redirect_chain"]),
        }
        normalized.append({
            **core,
            "entry_sha256": hashlib.sha256(
                canonical_bytes(core),
            ).hexdigest(),
        })
    normalized.sort(key=lambda item: item["entry_sha256"])
    core = {"schema_version": SCHEMA_VERSION, "entries": normalized}
    return {
        **core,
        "fetch_provenance_authority_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_fetch_provenance_authority(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("FETCH_PROVENANCE_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "entries",
        "fetch_provenance_authority_sha256",
    }
    if set(value) != required:
        raise IdentityWebEvidenceError("FETCH_PROVENANCE_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "fetch_provenance_authority_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "fetch_provenance_authority_sha256"
    ]:
        raise IdentityWebEvidenceError("FETCH_PROVENANCE_HASH_MISMATCH")
    for entry in value["entries"]:
        expected = hashlib.sha256(canonical_bytes({
            key: entry[key]
            for key in entry
            if key != "entry_sha256"
        })).hexdigest()
        if entry["entry_sha256"] != expected:
            raise IdentityWebEvidenceError("FETCH_PROVENANCE_ENTRY_HASH_MISMATCH")
    return value


def content_sha256(content):
    if not isinstance(content, bytes):
        raise IdentityWebEvidenceError("CONTENT_BYTES_REQUIRED")
    return hashlib.sha256(content).hexdigest()


def evidence_id(
    *,
    source_tier,
    source_kind,
    source_url,
    content_sha256_value,
    extractor_version=EXTRACTOR_VERSION,
):
    core = {
        "schema_version": SCHEMA_VERSION,
        "source_tier": source_tier,
        "source_kind": source_kind,
        "source_url": source_url,
        "content_sha256": _required_sha256(
            content_sha256_value,
            "CONTENT_SHA256",
        ),
        "extractor_version": extractor_version,
    }
    return "evidence_" + hashlib.sha256(
        canonical_bytes(core),
    ).hexdigest()[:24]


def normalize_claim(claim_type, raw_value):
    if claim_type not in CLAIM_TYPES:
        raise IdentityWebEvidenceError("CLAIM_TYPE_INVALID")
    text = _normalize_text(raw_value)
    if claim_type == "OFFICIAL_BIRTH_DATE":
        candidate = text.replace("/", "-").replace(".", "-")
        if not _DATE.fullmatch(candidate):
            raise IdentityWebEvidenceError("BIRTH_DATE_INVALID")
        text = candidate
    return text


def make_claim(
    *,
    claim_type,
    raw_value,
    source_locator,
    claim_confidence="MEDIUM",
    machine_extracted=True,
    human_verified=False,
):
    return {
        "claim_type": claim_type,
        "raw_value": _required_text(raw_value, "RAW_VALUE"),
        "normalized_value": normalize_claim(claim_type, raw_value),
        "source_locator": _required_text(
            source_locator,
            "SOURCE_LOCATOR",
        ),
        "claim_confidence": claim_confidence,
        "machine_extracted": machine_extracted,
        "human_verified": human_verified,
    }


def external_person_id_claim(
    *,
    namespace,
    identifier,
    source_semantics,
    source_locator,
):
    if source_semantics not in {"PLAYER_ENTITY", "PERSON_ENTITY"}:
        raise IdentityWebEvidenceError(
            "EXTERNAL_PERSON_SEMANTICS_REQUIRED",
        )
    namespace = _required_text(namespace, "EXTERNAL_ID_NAMESPACE")
    if not EXTERNAL_ID_NAMESPACE.fullmatch(namespace):
        raise IdentityWebEvidenceError("EXTERNAL_ID_NAMESPACE_INVALID")
    return make_claim(
        claim_type="OFFICIAL_SOURCE_DECLARED_PERSON_ID",
        raw_value=f"{namespace}:{_required_text(identifier, 'EXTERNAL_ID')}",
        source_locator=source_locator,
    )


def _validate_claim(claim):
    if not isinstance(claim, dict):
        raise IdentityWebEvidenceError("CLAIM_OBJECT_REQUIRED")
    required = {
        "claim_type",
        "raw_value",
        "normalized_value",
        "source_locator",
        "claim_confidence",
        "machine_extracted",
        "human_verified",
    }
    if set(claim) != required:
        raise IdentityWebEvidenceError("CLAIM_SCHEMA_INVALID")
    normalized = normalize_claim(claim["claim_type"], claim["raw_value"])
    if claim["normalized_value"] != normalized:
        raise IdentityWebEvidenceError("CLAIM_NORMALIZATION_MISMATCH")
    return claim


def _validate_evidence_binding(binding):
    if not isinstance(binding, dict):
        raise IdentityWebEvidenceError("EVIDENCE_BINDING_OBJECT_REQUIRED")
    required = {
        "record_key",
        "target_type",
        "target_id",
        "matched_claim_fields",
        "binding_rationale",
    }
    if set(binding) != required:
        raise IdentityWebEvidenceError("EVIDENCE_BINDING_SCHEMA_INVALID")
    _required_text(binding["record_key"], "RECORD_KEY")
    if binding["target_type"] not in {"EXISTING_UID", "NEW_GROUP"}:
        raise IdentityWebEvidenceError("EVIDENCE_TARGET_TYPE_INVALID")
    _required_text(binding["target_id"], "EVIDENCE_TARGET_ID")
    if not isinstance(binding["matched_claim_fields"], list):
        raise IdentityWebEvidenceError("MATCHED_CLAIM_FIELDS_REQUIRED")
    if binding["matched_claim_fields"] != sorted(
        set(binding["matched_claim_fields"]),
    ):
        raise IdentityWebEvidenceError("MATCHED_CLAIM_FIELDS_ORDER_INVALID")
    _required_text(binding["binding_rationale"], "BINDING_RATIONALE")
    return binding


def validate_evidence_item(item, *, b0_source_registry=None):
    if not isinstance(item, dict):
        raise IdentityWebEvidenceError("EVIDENCE_ITEM_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "evidence_id",
        "source_tier",
        "source_kind",
        "source_url",
        "source_domain",
        "fetched_at",
        "http_status",
        "content_type",
        "content_sha256",
        "raw_snapshot_path",
        "published_at",
        "extractor_version",
        "claims",
        "extraction_warnings",
        "record_keys",
        "bindings",
        "b0_registry_sha256",
        "b0_entry_id",
    }
    if set(item) != required:
        raise IdentityWebEvidenceError("EVIDENCE_ITEM_SCHEMA_INVALID")
    if item["schema_version"] != SCHEMA_VERSION:
        raise IdentityWebEvidenceError("EVIDENCE_ITEM_VERSION_INVALID")
    expected_id = evidence_id(
        source_tier=item["source_tier"],
        source_kind=item["source_kind"],
        source_url=item["source_url"],
        content_sha256_value=item["content_sha256"],
        extractor_version=item["extractor_version"],
    )
    if item["evidence_id"] != expected_id:
        raise IdentityWebEvidenceError("EVIDENCE_ID_MISMATCH")
    if item["source_domain"] != source_domain(item["source_url"]):
        raise IdentityWebEvidenceError("EVIDENCE_DOMAIN_MISMATCH")
    if item["source_tier"] == "B0":
        _required_sha256(item["b0_registry_sha256"], "B0_REGISTRY_SHA256")
        _required_text(item["b0_entry_id"], "B0_ENTRY_ID")
        registry = validate_b0_source_registry(b0_source_registry)
        if item["b0_registry_sha256"] != registry[
            "b0_source_registry_sha256"
        ]:
            raise IdentityWebEvidenceError("B0_REGISTRY_HASH_MISMATCH")
        entry = next((
            entry for entry in registry["entries"]
            if (
                entry["entry_id"] == item["b0_entry_id"]
                and entry["approved_domain"] == item["source_domain"]
            )
        ), None)
        if entry is None:
            raise IdentityWebEvidenceError("B0_DOMAIN_NOT_APPROVED")
    else:
        validate_source_url(
            item["source_url"],
            source_tier=item["source_tier"],
        )
    if not isinstance(item["claims"], list):
        raise IdentityWebEvidenceError("CLAIMS_REQUIRED")
    for claim in item["claims"]:
        _validate_claim(claim)
    if item["extraction_warnings"] != sorted(set(item["extraction_warnings"])):
        raise IdentityWebEvidenceError("WARNING_ORDER_INVALID")
    if item["record_keys"] != sorted(set(item["record_keys"])):
        raise IdentityWebEvidenceError("RECORD_KEYS_ORDER_INVALID")
    if not isinstance(item["bindings"], list):
        raise IdentityWebEvidenceError("EVIDENCE_BINDINGS_REQUIRED")
    claim_types = {claim["claim_type"] for claim in item["claims"]}
    for binding in item["bindings"]:
        _validate_evidence_binding(binding)
        if not binding["matched_claim_fields"]:
            raise IdentityWebEvidenceError("MATCHED_CLAIM_FIELDS_REQUIRED")
        if not set(binding["matched_claim_fields"]) <= claim_types:
            raise IdentityWebEvidenceError(
                "BINDING_MATCHED_CLAIM_NOT_PRESENT",
            )
    return item


def build_b0_source_registry(entries):
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise IdentityWebEvidenceError("B0_REGISTRY_ENTRY_INVALID")
        required = {
            "entry_id",
            "canonical_club_identity",
            "approved_domain",
            "human_approval_ref",
            "approved_at",
        }
        if set(entry) != required:
            raise IdentityWebEvidenceError("B0_REGISTRY_ENTRY_INVALID")
        for field in (
            "entry_id",
            "canonical_club_identity",
            "approved_domain",
            "human_approval_ref",
            "approved_at",
        ):
            _required_text(entry[field], f"B0_{field.upper()}")
        normalized.append({
            **entry,
            "approved_domain": entry["approved_domain"].lower(),
        })
    normalized.sort(key=lambda item: item["entry_id"])
    entry_ids = [item["entry_id"] for item in normalized]
    domains = [item["approved_domain"] for item in normalized]
    if len(entry_ids) != len(set(entry_ids)):
        raise IdentityWebEvidenceError("B0_ENTRY_ID_DUPLICATE")
    if len(domains) != len(set(domains)):
        raise IdentityWebEvidenceError("B0_DOMAIN_DUPLICATE")
    core = {
        "schema_version": SCHEMA_VERSION,
        "entries": normalized,
    }
    return {
        **core,
        "b0_source_registry_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def build_uid_bridge_authority(entries):
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise IdentityWebEvidenceError("UID_BRIDGE_ENTRY_INVALID")
        required = {
            "entry_id",
            "namespace",
            "identifier",
            "existing_uid",
            "approval_ref",
        }
        if set(entry) != required:
            raise IdentityWebEvidenceError("UID_BRIDGE_ENTRY_INVALID")
        normalized.append({
            **entry,
            "normalized_identifier": (
                f"{entry['namespace']}:{entry['identifier']}"
            ),
        })
    normalized.sort(key=lambda item: item["entry_id"])
    core = {
        "schema_version": SCHEMA_VERSION,
        "entries": normalized,
    }
    return {
        **core,
        "uid_bridge_authority_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_uid_bridge_authority(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("UID_BRIDGE_AUTHORITY_OBJECT_REQUIRED")
    required = {"schema_version", "entries", "uid_bridge_authority_sha256"}
    if set(value) != required:
        raise IdentityWebEvidenceError("UID_BRIDGE_AUTHORITY_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "uid_bridge_authority_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "uid_bridge_authority_sha256"
    ]:
        raise IdentityWebEvidenceError("UID_BRIDGE_AUTHORITY_HASH_MISMATCH")
    seen = set()
    for entry in value["entries"]:
        for field in (
            "entry_id",
            "namespace",
            "identifier",
            "existing_uid",
            "approval_ref",
            "normalized_identifier",
        ):
            _required_text(entry.get(field), f"UID_BRIDGE_{field.upper()}")
        if entry["normalized_identifier"] != (
            f"{entry['namespace']}:{entry['identifier']}"
        ):
            raise IdentityWebEvidenceError(
                "UID_BRIDGE_NORMALIZED_IDENTIFIER_MISMATCH",
            )
        if entry["entry_id"] in seen:
            raise IdentityWebEvidenceError("UID_BRIDGE_ENTRY_DUPLICATE")
        seen.add(entry["entry_id"])
    return value


def validate_b0_source_registry(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("B0_REGISTRY_OBJECT_REQUIRED")
    required = {"schema_version", "entries", "b0_source_registry_sha256"}
    if set(value) != required:
        raise IdentityWebEvidenceError("B0_REGISTRY_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "b0_source_registry_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "b0_source_registry_sha256"
    ]:
        raise IdentityWebEvidenceError("B0_REGISTRY_HASH_MISMATCH")
    entry_ids = []
    domains = []
    for entry in value["entries"]:
        if not isinstance(entry, dict):
            raise IdentityWebEvidenceError("B0_REGISTRY_ENTRY_INVALID")
        for field in (
            "entry_id",
            "canonical_club_identity",
            "approved_domain",
            "human_approval_ref",
            "approved_at",
        ):
            _required_text(entry.get(field), f"B0_{field.upper()}")
        entry_ids.append(entry["entry_id"])
        domains.append(entry["approved_domain"].lower())
    if len(entry_ids) != len(set(entry_ids)):
        raise IdentityWebEvidenceError("B0_ENTRY_ID_DUPLICATE")
    if len(domains) != len(set(domains)):
        raise IdentityWebEvidenceError("B0_DOMAIN_DUPLICATE")
    return value


def build_evidence_item(
    *,
    source_tier,
    source_kind,
    source_url,
    fetched_at,
    http_status,
    content_type,
    content,
    raw_snapshot_path,
    claims,
    record_keys,
    published_at=None,
    extraction_warnings=None,
    extractor_version=EXTRACTOR_VERSION,
    private_b0_domains=None,
    bindings=None,
    b0_registry_sha256=None,
    b0_entry_id=None,
    b0_source_registry=None,
):
    if source_tier == "B0":
        registry = validate_b0_source_registry(b0_source_registry)
        if b0_registry_sha256 != registry["b0_source_registry_sha256"]:
            raise IdentityWebEvidenceError("B0_REGISTRY_HASH_MISMATCH")
        entry = next(
            (
                item for item in registry["entries"]
                if (
                    item["entry_id"] == b0_entry_id
                    and item["approved_domain"] == source_domain(source_url)
                )
            ),
            None,
        )
        if entry is None:
            raise IdentityWebEvidenceError("B0_DOMAIN_NOT_APPROVED")
    else:
        validate_source_url(
            source_url,
            source_tier=source_tier,
            private_b0_domains=private_b0_domains,
        )
    digest = content_sha256(content)
    item_id = evidence_id(
        source_tier=source_tier,
        source_kind=source_kind,
        source_url=source_url,
        content_sha256_value=digest,
        extractor_version=extractor_version,
    )
    item = {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": item_id,
        "source_tier": source_tier,
        "source_kind": _required_text(source_kind, "SOURCE_KIND"),
        "source_url": source_url,
        "source_domain": source_domain(source_url),
        "fetched_at": _required_text(fetched_at, "FETCHED_AT"),
        "http_status": http_status,
        "content_type": _required_text(content_type, "CONTENT_TYPE"),
        "content_sha256": digest,
        "raw_snapshot_path": _required_text(
            raw_snapshot_path,
            "RAW_SNAPSHOT_PATH",
        ),
        "published_at": published_at,
        "extractor_version": extractor_version,
        "claims": sorted(
            [_validate_claim(claim) for claim in claims],
            key=lambda item: (
                item["claim_type"],
                item["normalized_value"],
                item["source_locator"],
            ),
        ),
        "extraction_warnings": sorted(set(extraction_warnings or [])),
        "record_keys": sorted(set(record_keys)),
        "bindings": sorted(
            [_validate_evidence_binding(item) for item in bindings or []],
            key=lambda item: (
                item["record_key"],
                item["target_type"],
                item["target_id"],
            ),
        ),
        "b0_registry_sha256": b0_registry_sha256,
        "b0_entry_id": b0_entry_id,
    }
    return validate_evidence_item(
        item,
        b0_source_registry=b0_source_registry,
    )


class _ClaimHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.claims = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        claim_type = data.get("data-claim-type")
        value = data.get("data-claim-value")
        if claim_type and value:
            self.claims.append(make_claim(
                claim_type=claim_type,
                raw_value=html.unescape(value),
                source_locator=data.get("data-claim-locator") or tag,
            ))


def extract_html_claims(content, *, source_locator="html"):
    if not isinstance(content, (bytes, str)):
        raise IdentityWebEvidenceError("HTML_CONTENT_REQUIRED")
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    parser = _ClaimHTMLParser()
    parser.feed(text)
    return parser.claims


def extract_pdf_claims(content, *, text_extractor=None):
    if not isinstance(content, bytes):
        raise IdentityWebEvidenceError("PDF_BYTES_REQUIRED")
    if text_extractor is None:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise IdentityWebEvidenceError("PDF_EXTRACTOR_REQUIRED") from exc
        reader = PdfReader(io.BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = text_extractor(content)
    claims = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in CLAIM_TYPES:
            claims.append(make_claim(
                claim_type=key,
                raw_value=value,
                source_locator=f"pdf-line:{line_number}",
            ))
    return claims


def build_evidence_manifest(items, *, b0_source_registry=None):
    normalized = sorted(
        [
            validate_evidence_item(
                item,
                b0_source_registry=b0_source_registry,
            )
            for item in items
        ],
        key=lambda item: item["evidence_id"],
    )
    ids = [item["evidence_id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise IdentityWebEvidenceError("EVIDENCE_ID_DUPLICATE")
    core = {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "items": normalized,
    }
    return {
        **core,
        "web_evidence_manifest_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_evidence_manifest(value, *, b0_source_registry=None):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("EVIDENCE_MANIFEST_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "extractor_version",
        "items",
        "web_evidence_manifest_sha256",
    }
    if set(value) != required:
        raise IdentityWebEvidenceError("EVIDENCE_MANIFEST_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "web_evidence_manifest_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "web_evidence_manifest_sha256"
    ]:
        raise IdentityWebEvidenceError("EVIDENCE_MANIFEST_HASH_MISMATCH")
    for item in value["items"]:
        validate_evidence_item(item, b0_source_registry=b0_source_registry)
    return value


def _claims_for_record(manifest, record_key):
    claims = []
    for item in manifest["items"]:
        if record_key not in item["record_keys"]:
            continue
        for claim in item["claims"]:
            claims.append({**claim, "evidence_id": item["evidence_id"]})
    return claims


def _claims_for_target(manifest, record_key, target_type, target_id):
    claims = []
    for item in manifest["items"]:
        if record_key not in item["record_keys"]:
            continue
        matching_binding = next((
            binding for binding in item["bindings"]
            if (
                binding["record_key"] == record_key
                and binding["target_type"] == target_type
                and binding["target_id"] == target_id
            )
        ), None)
        if matching_binding is None:
            continue
        for claim in item["claims"]:
            claims.append({
                **claim,
                "evidence_id": item["evidence_id"],
                "binding_rationale": matching_binding["binding_rationale"],
                "matched_claim_fields": list(
                    matching_binding["matched_claim_fields"],
                ),
            })
    return claims


def bind_evidence_to_target(
    *,
    record_key,
    target_type,
    target_id,
    matched_claim_fields,
    binding_rationale,
):
    return _validate_evidence_binding({
        "record_key": record_key,
        "target_type": target_type,
        "target_id": target_id,
        "matched_claim_fields": sorted(set(matched_claim_fields)),
        "binding_rationale": binding_rationale,
    })


def classify_candidate_evidence(
    record_key,
    *,
    target_type,
    target_id,
    manifest,
):
    validate_evidence_manifest(manifest)
    claims = _claims_for_target(
        manifest,
        record_key,
        target_type,
        target_id,
    )
    if not claims:
        return "W4_DISCOVERY_SUPPORT", ["NO_TARGET_BOUND_OFFICIAL_EVIDENCE"]
    evidence_ids = {claim["evidence_id"] for claim in claims}
    person_ids = _person_ids(claims)
    if len(person_ids) > 1:
        return "WX_CONFLICT", ["OFFICIAL_PERSON_ID_CONFLICT"]
    birth_dates = {
        claim["normalized_value"]
        for claim in claims
        if claim["claim_type"] == "OFFICIAL_BIRTH_DATE"
    }
    if len(birth_dates) > 1:
        return "WX_CONFLICT", ["OFFICIAL_BIRTH_DATE_CONFLICT"]
    if person_ids:
        return "W1_OFFICIAL_PERSON_ID_EXACT", []
    names = {
        claim["normalized_value"]
        for claim in claims
        if claim["claim_type"] == "OFFICIAL_PLAYER_NAME"
    }
    if names and birth_dates and len(evidence_ids) >= 2:
        return "W2_OFFICIAL_BIO_MULTI_SOURCE", []
    if names and any(
        claim["claim_type"] in {
            "OFFICIAL_TEAM",
            "OFFICIAL_SEASON",
            "OFFICIAL_REGISTRATION_UNIT",
        }
        for claim in claims
    ):
        return "W3_OFFICIAL_CONTINUITY", []
    return "W4_DISCOVERY_SUPPORT", []


def _person_ids(claims):
    return {
        claim["normalized_value"]
        for claim in claims
        if claim["claim_type"] == "OFFICIAL_SOURCE_DECLARED_PERSON_ID"
    }


def classify_record_evidence(record_key, manifest):
    validate_evidence_manifest(manifest)
    claims = _claims_for_record(manifest, record_key)
    evidence_ids = {claim["evidence_id"] for claim in claims}
    person_ids = _person_ids(claims)
    if len(person_ids) > 1:
        return "WX_CONFLICT", ["OFFICIAL_PERSON_ID_CONFLICT"]
    birth_dates = {
        claim["normalized_value"]
        for claim in claims
        if claim["claim_type"] == "OFFICIAL_BIRTH_DATE"
    }
    if len(birth_dates) > 1:
        return "WX_CONFLICT", ["OFFICIAL_BIRTH_DATE_CONFLICT"]
    if person_ids:
        return "W1_OFFICIAL_PERSON_ID_EXACT", []
    names = {
        claim["normalized_value"]
        for claim in claims
        if claim["claim_type"] == "OFFICIAL_PLAYER_NAME"
    }
    if names and birth_dates and len(evidence_ids) >= 2:
        return "W2_OFFICIAL_BIO_MULTI_SOURCE", []
    if names and any(
        claim["claim_type"] in {
            "OFFICIAL_TEAM",
            "OFFICIAL_SEASON",
            "OFFICIAL_REGISTRATION_UNIT",
        }
        for claim in claims
    ):
        return "W3_OFFICIAL_CONTINUITY", []
    return "W4_DISCOVERY_SUPPORT", []


def deterministic_group_id(
    member_review_ids,
    evidence_class,
    evidence_ids=None,
):
    core = {
        "member_review_ids": sorted(set(member_review_ids)),
        "evidence_class": evidence_class,
        "evidence_ids": sorted(set(evidence_ids or [])),
    }
    return "candidate_group_" + hashlib.sha256(
        canonical_bytes(core),
    ).hexdigest()[:24]


def build_conflicts(manifest):
    validate_evidence_manifest(manifest)
    conflicts = []
    record_keys = sorted({
        record_key
        for item in manifest["items"]
        for record_key in item["record_keys"]
    })
    for record_key in record_keys:
        evidence_class, reasons = classify_record_evidence(
            record_key,
            manifest,
        )
        if evidence_class == "WX_CONFLICT":
            conflicts.append({
                "record_key": record_key,
                "conflict_class": "WX_CONFLICT",
                "reasons": reasons,
            })
    return conflicts


def batch_predicate(proposal):
    if proposal.get("conflict_count", 0):
        return None
    if proposal.get("text_corruption_warning"):
        return None
    if (
        proposal.get("proposal_type") == "EXISTING_IDENTITY_CANDIDATE"
        and proposal.get("evidence_class") == "W1_OFFICIAL_PERSON_ID_EXACT"
    ):
        return "BATCH_EXISTING_W1"
    if (
        proposal.get("proposal_type") == "EXISTING_IDENTITY_CANDIDATE"
        and proposal.get("evidence_class") == "W2_OFFICIAL_BIO_MULTI_SOURCE"
    ):
        return "BATCH_EXISTING_W2"
    if (
        proposal.get("proposal_type") == "NO_SAFE_CANDIDATE"
        and proposal.get("search_status")
        == "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"
    ):
        return "BATCH_NO_SAFE_SEARCH_EXHAUSTED"
    return None


def build_collector_search_manifest(
    *,
    record_key,
    applicable_required_collectors,
    completed_collectors,
    failed_collectors,
    attempted_official_urls,
    collector_version,
    text_warning,
    identity_conflict,
    surviving_candidate_count,
):
    core = {
        "schema_version": SCHEMA_VERSION,
        "record_key": record_key,
        "applicable_required_collectors": sorted(
            set(applicable_required_collectors),
        ),
        "completed_collectors": sorted(set(completed_collectors)),
        "failed_collectors": sorted(set(failed_collectors)),
        "attempted_official_urls": sorted(set(attempted_official_urls)),
        "collector_version": collector_version,
        "text_warning": bool(text_warning),
        "identity_conflict": bool(identity_conflict),
        "surviving_candidate_count": surviving_candidate_count,
    }
    return {
        **core,
        "collector_search_manifest_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_collector_search_manifest(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("SEARCH_MANIFEST_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "record_key",
        "applicable_required_collectors",
        "completed_collectors",
        "failed_collectors",
        "attempted_official_urls",
        "collector_version",
        "text_warning",
        "identity_conflict",
        "surviving_candidate_count",
        "collector_search_manifest_sha256",
    }
    if set(value) != required:
        raise IdentityWebEvidenceError("SEARCH_MANIFEST_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "collector_search_manifest_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "collector_search_manifest_sha256"
    ]:
        raise IdentityWebEvidenceError("SEARCH_MANIFEST_HASH_MISMATCH")
    return value


def search_exhaustion_status(search_manifest):
    validate_collector_search_manifest(search_manifest)
    eligible = (
        search_manifest["applicable_required_collectors"]
        and set(search_manifest["applicable_required_collectors"])
        == set(search_manifest["completed_collectors"])
        and not search_manifest["failed_collectors"]
        and not search_manifest["text_warning"]
        and not search_manifest["identity_conflict"]
        and search_manifest["surviving_candidate_count"] == 0
    )
    return (
        "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"
        if eligible
        else "SEARCH_INCOMPLETE_OR_CONFLICT"
    )


def enrich_candidates(
    candidates,
    *,
    evidence_manifest,
    search_statuses=None,
    association_contexts=None,
    bridge_authority=None,
):
    if (
        association_contexts is not None
        and bridge_authority is not None
    ):
        evidence_manifest = apply_existing_uid_bindings(
            evidence_manifest,
            association_contexts,
            bridge_authority,
        )
    validate_evidence_manifest(evidence_manifest)
    search_statuses = search_statuses or {}
    conflicts = {
        item["record_key"] for item in build_conflicts(evidence_manifest)
    }
    enriched = []
    for proposal in candidates:
        record_key = proposal["record_key"]
        search_manifest = search_statuses.get(record_key)
        search_status = (
            search_exhaustion_status(search_manifest)
            if isinstance(search_manifest, dict)
            else "SEARCH_NOT_ATTEMPTED"
        )
        if proposal.get("candidate_player_uid"):
            evidence_class, reasons = classify_candidate_evidence(
                record_key,
                target_type="EXISTING_UID",
                target_id=proposal["candidate_player_uid"],
                manifest=evidence_manifest,
            )
        elif proposal.get("candidate_group_id"):
            evidence_class, reasons = classify_candidate_evidence(
                record_key,
                target_type="NEW_GROUP",
                target_id=proposal["candidate_group_id"],
                manifest=evidence_manifest,
            )
        else:
            evidence_class, reasons = "W4_DISCOVERY_SUPPORT", [
                "NO_EXPLICIT_CANDIDATE_TARGET",
            ]
        item = {
            **proposal,
            "evidence_class": evidence_class,
            "conflict_count": int(evidence_class == "WX_CONFLICT"),
            "conflict_reasons": reasons,
            "search_status": search_status,
            "text_corruption_warning": any(
                _SUSPICIOUS_TEXT.search(claim["raw_value"])
                for claim in _claims_for_record(evidence_manifest, record_key)
            ),
            "batch_predicate": None,
        }
        if proposal["proposal_type"] == "NO_SAFE_CANDIDATE":
            if item["search_status"] == (
                "OFFICIAL_SEARCH_EXHAUSTED_NO_SAFE_MATCH"
            ):
                item["proposal_type"] = "NO_SAFE_CANDIDATE"
                item["machine_reason"] = item["search_status"]
            else:
                item["proposal_type"] = "NO_SAFE_CANDIDATE"
                item["proposed_relation"] = "NO_SAFE_CANDIDATE"
                item["machine_reason"] = "no_target_bound_safe_candidate"
        item["batch_predicate"] = batch_predicate(item)
        enriched.append(item)
    if association_contexts is not None:
        derived = derive_new_identity_groups(
            candidates,
            evidence_manifest=evidence_manifest,
            association_contexts=association_contexts,
        )
        derived_keys = {item["record_key"] for item in derived}
        enriched = [
            item for item in enriched
            if item["record_key"] not in derived_keys
        ]
        enriched.extend(derived)
    return sorted(
        enriched,
        key=lambda item: (
            item["record_key"],
            item["proposal_type"],
            item.get("candidate_player_uid") or "",
            item.get("candidate_group_id") or "",
        ),
    )


def validate_association_contexts(value):
    if not isinstance(value, list):
        raise IdentityWebEvidenceError("ASSOCIATION_CONTEXTS_REQUIRED")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise IdentityWebEvidenceError("ASSOCIATION_CONTEXT_INVALID")
        required = {
            "record_key",
            "discovered_url",
            "record_name",
            "record_birth_date",
        }
        if set(item) != required:
            raise IdentityWebEvidenceError("ASSOCIATION_CONTEXT_INVALID")
        normalized.append(item)
    return normalized


def build_association_authority(evidence_manifest, association_contexts):
    validate_evidence_manifest(evidence_manifest)
    contexts = validate_association_contexts(association_contexts)
    by_url = defaultdict(list)
    for context in contexts:
        by_url[context["discovered_url"]].append(context)
    entries = []
    for item in evidence_manifest["items"]:
        official_names = {
            claim["normalized_value"]
            for claim in item["claims"]
            if claim["claim_type"] == "OFFICIAL_PLAYER_NAME"
        }
        official_dobs = {
            claim["normalized_value"]
            for claim in item["claims"]
            if claim["claim_type"] == "OFFICIAL_BIRTH_DATE"
        }
        warnings = []
        if _SUSPICIOUS_TEXT.search(item["source_url"]) or any(
            _SUSPICIOUS_TEXT.search(claim["raw_value"])
            for claim in item["claims"]
        ):
            warnings.append("TEXT_OR_OCR_WARNING")
        for context in by_url.get(item["source_url"], []):
            private_name = normalize_claim(
                "OFFICIAL_PLAYER_NAME",
                context["record_name"],
            )
            name_match = bool(official_names) and private_name in official_names
            dob_match = (
                not context["record_birth_date"]
                or not official_dobs
                or context["record_birth_date"] in official_dobs
            )
            conflicts = []
            if official_names and not name_match:
                conflicts.append("RECORD_NAME_CONFLICT")
            if (
                context["record_birth_date"]
                and official_dobs
                and context["record_birth_date"] not in official_dobs
            ):
                conflicts.append("RECORD_DOB_CONFLICT")
            association_class = (
                "STRONG"
                if (
                    name_match
                    and dob_match
                    and not conflicts
                    and not warnings
                )
                else "WEAK"
            )
            core = {
                "record_key": context["record_key"],
                "evidence_id": item["evidence_id"],
                "association_class": association_class,
                "matched_private_fields": (
                    ["record_name"] if name_match else []
                ) + (
                    ["record_birth_date"] if dob_match and official_dobs else []
                ),
                "matched_official_claims": sorted({
                    claim["claim_type"]
                    for claim in item["claims"]
                    if claim["claim_type"] in {
                        "OFFICIAL_PLAYER_NAME",
                        "OFFICIAL_BIRTH_DATE",
                    }
                }),
                "association_warnings": sorted(set(warnings)),
                "association_conflicts": sorted(set(conflicts)),
            }
            entries.append({
                **core,
                "association_sha256": hashlib.sha256(
                    canonical_bytes(core),
                ).hexdigest(),
            })
    authority_core = {
        "schema_version": SCHEMA_VERSION,
        "entries": sorted(
            entries,
            key=lambda item: (
                item["record_key"],
                item["evidence_id"],
                item["association_sha256"],
            ),
        ),
    }
    return {
        **authority_core,
        "association_authority_sha256": hashlib.sha256(
            canonical_bytes(authority_core),
        ).hexdigest(),
    }


def validate_association_authority(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("ASSOCIATION_AUTHORITY_OBJECT_REQUIRED")
    required = {"schema_version", "entries", "association_authority_sha256"}
    if set(value) != required:
        raise IdentityWebEvidenceError("ASSOCIATION_AUTHORITY_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "association_authority_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "association_authority_sha256"
    ]:
        raise IdentityWebEvidenceError("ASSOCIATION_AUTHORITY_HASH_MISMATCH")
    for entry in value["entries"]:
        expected = hashlib.sha256(canonical_bytes({
            key: entry[key]
            for key in entry
            if key != "association_sha256"
        })).hexdigest()
        if entry["association_sha256"] != expected:
            raise IdentityWebEvidenceError("ASSOCIATION_HASH_MISMATCH")
    return value


def recompute_association_authority(
    evidence_manifest,
    association_contexts,
    supplied_authority,
):
    expected = build_association_authority(
        evidence_manifest,
        association_contexts,
    )
    if expected != supplied_authority:
        raise IdentityWebEvidenceError("ASSOCIATION_AUTHORITY_STALE")
    return expected


def derive_existing_uid_bindings(
    evidence_manifest,
    association_contexts,
    bridge_authority,
    association_authority=None,
):
    validate_evidence_manifest(evidence_manifest)
    contexts = validate_association_contexts(association_contexts)
    bridge = validate_uid_bridge_authority(bridge_authority)
    by_url = defaultdict(list)
    for context in contexts:
        by_url[context["discovered_url"]].append(context)
    by_identifier = {
        item["normalized_identifier"]: item
        for item in bridge["entries"]
    }
    allowed_associations = None
    if association_authority is not None:
        authority = recompute_association_authority(
            evidence_manifest,
            contexts,
            association_authority,
        )
        allowed_associations = {
            (item["record_key"], item["evidence_id"])
            for item in authority["entries"]
            if (
                item["association_class"] == "STRONG"
                and not item["association_conflicts"]
                and not item["association_warnings"]
            )
        }
    bindings = []
    for item in evidence_manifest["items"]:
        identifiers = _person_ids(item["claims"])
        if len(identifiers) != 1:
            continue
        bridge_entry = by_identifier.get(next(iter(identifiers)))
        if bridge_entry is None:
            continue
        for context in by_url.get(item["source_url"], []):
            if (
                allowed_associations is not None
                and (context["record_key"], item["evidence_id"])
                not in allowed_associations
            ):
                continue
            record_name = normalize_claim(
                "OFFICIAL_PLAYER_NAME",
                context["record_name"],
            )
            name_claims = {
                claim["normalized_value"]
                for claim in item["claims"]
                if claim["claim_type"] == "OFFICIAL_PLAYER_NAME"
            }
            if name_claims and record_name not in name_claims:
                continue
            bindings.append(_validate_evidence_binding({
                "record_key": context["record_key"],
                "target_type": "EXISTING_UID",
                "target_id": bridge_entry["existing_uid"],
                "matched_claim_fields": [
                    "OFFICIAL_SOURCE_DECLARED_PERSON_ID",
                ],
                "binding_rationale": (
                    f"bridge:{bridge_entry['entry_id']}"
                ),
            }))
    return bindings


def apply_existing_uid_bindings(
    evidence_manifest,
    association_contexts,
    bridge_authority,
    association_authority=None,
):
    bindings = derive_existing_uid_bindings(
        evidence_manifest,
        association_contexts,
        bridge_authority,
        association_authority=association_authority,
    )
    by_url = defaultdict(list)
    for context in validate_association_contexts(association_contexts):
        by_url[context["discovered_url"]].append(context)
    items = []
    for item in evidence_manifest["items"]:
        item_bindings = list(item["bindings"])
        for context in by_url.get(item["source_url"], []):
            for binding in bindings:
                if (
                    binding["record_key"] == context["record_key"]
                    and binding["target_type"] == "EXISTING_UID"
                ):
                    item_bindings.append(binding)
        items.append({
            **item,
            "bindings": sorted(
                item_bindings,
                key=lambda value: (
                    value["record_key"],
                    value["target_type"],
                    value["target_id"],
                ),
            ),
        })
    return build_evidence_manifest(items)


def derive_new_identity_groups(
    candidates,
    *,
    evidence_manifest,
    association_contexts,
    association_authority=None,
):
    validate_evidence_manifest(evidence_manifest)
    contexts = {
        item["record_key"]: item
        for item in validate_association_contexts(association_contexts)
    }
    allowed_associations = None
    if association_authority is not None:
        authority = recompute_association_authority(
            evidence_manifest,
            list(contexts.values()),
            association_authority,
        )
        allowed_associations = {
            (item["record_key"], item["evidence_id"])
            for item in authority["entries"]
            if (
                item["association_class"] == "STRONG"
                and not item["association_conflicts"]
                and not item["association_warnings"]
            )
        }
    candidates_by_key = {
        item["record_key"]: item for item in candidates
        if item.get("proposal_type") == "NO_SAFE_CANDIDATE"
    }
    key_members = defaultdict(set)
    key_evidence = defaultdict(set)
    key_class = {}
    for item in evidence_manifest["items"]:
        person_ids = _person_ids(item["claims"])
        if len(person_ids) == 1:
            person_id = next(iter(person_ids))
            for context in contexts.values():
                if context["discovered_url"] != item["source_url"]:
                    continue
                record_key = context["record_key"]
                if (
                    allowed_associations is not None
                    and (record_key, item["evidence_id"])
                    not in allowed_associations
                ):
                    continue
                if record_key not in candidates_by_key:
                    continue
                key_members[("W1", person_id)].add(record_key)
                key_evidence[("W1", person_id)].add(item["evidence_id"])
                key_class[("W1", person_id)] = (
                    "W1_OFFICIAL_PERSON_ID_EXACT"
                )
        names = {
            claim["normalized_value"]
            for claim in item["claims"]
            if claim["claim_type"] == "OFFICIAL_PLAYER_NAME"
        }
        dates = {
            claim["normalized_value"]
            for claim in item["claims"]
            if claim["claim_type"] == "OFFICIAL_BIRTH_DATE"
        }
        if len(names) == 1 and len(dates) == 1:
            extra = any(
                claim["claim_type"] in {
                    "OFFICIAL_REGISTRATION_UNIT",
                    "OFFICIAL_TEAM",
                    "OFFICIAL_JERSEY_NUMBER",
                }
                for claim in item["claims"]
            )
            if extra:
                for context in contexts.values():
                    if context["discovered_url"] != item["source_url"]:
                        continue
                    record_key = context["record_key"]
                    if (
                        allowed_associations is not None
                        and (record_key, item["evidence_id"])
                        not in allowed_associations
                    ):
                        continue
                    if record_key not in candidates_by_key:
                        continue
                    key = ("W2", next(iter(names)), next(iter(dates)))
                    key_members[key].add(record_key)
                    key_evidence[key].add(item["evidence_id"])
                    key_class[key] = "W2_OFFICIAL_BIO_MULTI_SOURCE"
    grouped = []
    for key, record_keys in sorted(key_members.items()):
        if len(record_keys) < 1:
            continue
        evidence_ids = sorted(key_evidence[key])
        if key[0] == "W2" and len(evidence_ids) < 2:
            continue
        member_review_ids = [
            candidates_by_key[record_key].get("review_id") or record_key
            for record_key in sorted(record_keys)
        ]
        group_id = deterministic_group_id(
            member_review_ids,
            key_class[key],
            evidence_ids,
        )
        for record_key in sorted(record_keys):
            item = candidates_by_key[record_key]
            grouped.append({
                **item,
                "proposal_type": "NEW_IDENTITY_CANDIDATE",
                "candidate_group_id": group_id,
                "candidate_player_uid": None,
                "proposed_relation": "KEEP_UNDECIDED",
                "machine_reason": "shared_target_bound_official_evidence",
                "evidence_class": key_class[key],
                "evidence_refs": evidence_ids,
            })
    return sorted(grouped, key=lambda item: (
        item["candidate_group_id"],
        item["record_key"],
    ))


def build_batch_plan(
    packet,
    evidence_manifest,
    *,
    batch_size=200,
    search_statuses=None,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    search_statuses = search_statuses or {}
    eligible = []
    for proposal in packet["reviews"]:
        claims = _claims_for_record(evidence_manifest, proposal["record_key"])
        if proposal.get("candidate_player_uid"):
            evidence_class, reasons = classify_candidate_evidence(
                proposal["record_key"],
                target_type="EXISTING_UID",
                target_id=proposal["candidate_player_uid"],
                manifest=evidence_manifest,
            )
        elif proposal.get("candidate_group_id"):
            evidence_class, reasons = classify_candidate_evidence(
                proposal["record_key"],
                target_type="NEW_GROUP",
                target_id=proposal["candidate_group_id"],
                manifest=evidence_manifest,
            )
        else:
            evidence_class, reasons = "W4_DISCOVERY_SUPPORT", []
        search_manifest = search_statuses.get(proposal["record_key"])
        search_status = (
            search_exhaustion_status(search_manifest)
            if isinstance(search_manifest, dict)
            else "SEARCH_NOT_ATTEMPTED"
        )
        item = {
            **proposal,
            "evidence_class": evidence_class,
            "conflict_count": int(evidence_class == "WX_CONFLICT"),
            "text_corruption_warning": any(
                _SUSPICIOUS_TEXT.search(claim["raw_value"])
                for claim in claims
            ),
            "search_status": search_status,
        }
        pred = batch_predicate(item)
        if pred is not None:
            eligible.append({**item, "batch_predicate": pred})
    eligible.sort(key=lambda item: (
        item["batch_predicate"],
        item["record_key"],
        item["review_id"],
    ))
    batches = []
    collector_manifest_hashes = {}
    by_predicate = defaultdict(list)
    for item in eligible:
        by_predicate[item["batch_predicate"]].append(item)
    for predicate in sorted(by_predicate):
        predicate_rows = sorted(
            by_predicate[predicate],
            key=lambda item: (item["record_key"], item["review_id"]),
        )
        for start in range(0, len(predicate_rows), batch_size):
            members = predicate_rows[start:start + batch_size]
            batches.append({
                "batch_id": "batch_" + hashlib.sha256(
                    canonical_bytes([
                        item["review_id"] for item in members
                    ]),
                ).hexdigest()[:24],
                "batch_predicate": predicate,
                "member_review_ids": [item["review_id"] for item in members],
                "member_count": len(members),
                "collector_manifest_hashes": {
                    item["record_key"]: (
                        search_statuses[item["record_key"]][
                            "collector_search_manifest_sha256"
                        ]
                        if isinstance(
                            search_statuses.get(item["record_key"]),
                            dict,
                        )
                        else None
                    )
                    for item in members
                },
            })
    for record_key, value in search_statuses.items():
        if isinstance(value, dict):
            collector_manifest_hashes[record_key] = value[
                "collector_search_manifest_sha256"
            ]
    return {
        "schema_version": SCHEMA_VERSION,
        "authority": "NON_CANONICAL_HUMAN_WORKFLOW_PROJECTION",
        "review_packet_sha256": packet["review_packet_sha256"],
        "web_evidence_manifest_sha256": evidence_manifest[
            "web_evidence_manifest_sha256"
        ],
        "batch_size": batch_size,
        "collector_search_manifest_set_sha256": hashlib.sha256(
            canonical_bytes(collector_manifest_hashes),
        ).hexdigest(),
        "batches": batches,
    }


def build_groups(packet, evidence_manifest):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    grouped = defaultdict(list)
    for proposal in packet["reviews"]:
        group_id = proposal.get("candidate_group_id")
        if not group_id:
            continue
        evidence_class, reasons = classify_candidate_evidence(
            proposal["record_key"],
            target_type="NEW_GROUP",
            target_id=group_id,
            manifest=evidence_manifest,
        )
        if (
            proposal["proposal_type"] == "NEW_IDENTITY_CANDIDATE"
            and evidence_class in {
                "W1_OFFICIAL_PERSON_ID_EXACT",
                "W2_OFFICIAL_BIO_MULTI_SOURCE",
            }
            and not reasons
        ):
            grouped[group_id].append(
                proposal["review_id"],
            )
    return [
        {
            "candidate_group_id": group_id,
            "member_review_ids": sorted(member_ids),
            "member_count": len(member_ids),
            "evidence_class": classify_candidate_evidence(
                next(
                    item["record_key"]
                    for item in packet["reviews"]
                    if item["candidate_group_id"] == group_id
                ),
                target_type="NEW_GROUP",
                target_id=group_id,
                manifest=evidence_manifest,
            )[0],
            "conflict_count": 0,
        }
        for group_id, member_ids in sorted(grouped.items())
    ]


def build_group_authority(
    packet,
    evidence_manifest,
    *,
    association_authority,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    validate_association_authority(association_authority)
    groups = build_groups(packet, evidence_manifest)
    core = {
        "schema_version": SCHEMA_VERSION,
        "review_packet_sha256": packet["review_packet_sha256"],
        "web_evidence_manifest_sha256": evidence_manifest[
            "web_evidence_manifest_sha256"
        ],
        "association_authority_sha256": association_authority[
            "association_authority_sha256"
        ],
        "groups": groups,
    }
    return {
        **core,
        "group_authority_sha256": hashlib.sha256(
            canonical_bytes(core),
        ).hexdigest(),
    }


def validate_group_authority(value):
    if not isinstance(value, dict):
        raise IdentityWebEvidenceError("GROUP_AUTHORITY_OBJECT_REQUIRED")
    required = {
        "schema_version",
        "review_packet_sha256",
        "web_evidence_manifest_sha256",
        "association_authority_sha256",
        "groups",
        "group_authority_sha256",
    }
    if set(value) != required:
        raise IdentityWebEvidenceError("GROUP_AUTHORITY_SCHEMA_INVALID")
    core = {
        key: value[key]
        for key in value
        if key != "group_authority_sha256"
    }
    if hashlib.sha256(canonical_bytes(core)).hexdigest() != value[
        "group_authority_sha256"
    ]:
        raise IdentityWebEvidenceError("GROUP_AUTHORITY_HASH_MISMATCH")
    return value


def recompute_groups(
    packet,
    evidence_manifest,
    group_authority,
    *,
    association_authority=None,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    authority = validate_group_authority(group_authority)
    if authority["review_packet_sha256"] != packet[
        "review_packet_sha256"
    ]:
        raise IdentityWebEvidenceError("GROUP_PLAN_PACKET_MISMATCH")
    if authority["web_evidence_manifest_sha256"] != evidence_manifest[
        "web_evidence_manifest_sha256"
    ]:
        raise IdentityWebEvidenceError("GROUP_PLAN_EVIDENCE_MISMATCH")
    if association_authority is not None:
        association = validate_association_authority(
            association_authority,
        )
        if authority["association_authority_sha256"] != association[
            "association_authority_sha256"
        ]:
            raise IdentityWebEvidenceError(
                "GROUP_PLAN_ASSOCIATION_MISMATCH",
            )
    expected = build_groups(packet, evidence_manifest)
    if expected != authority["groups"]:
        raise IdentityWebEvidenceError("GROUP_PLAN_STALE")
    return expected


def recompute_batches(
    packet,
    evidence_manifest,
    search_statuses,
    *,
    batch_size=200,
):
    expected = build_batch_plan(
        packet,
        evidence_manifest,
        batch_size=batch_size,
        search_statuses=search_statuses,
    )
    return expected["batches"]


def plan_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _validate_authority_event_common(event, packet, evidence_manifest):
    if packet["review_packet_sha256"] != event["review_packet_sha256"]:
        raise IdentityWebEvidenceError("REVIEW_PACKET_HASH_CHANGED")
    if evidence_manifest["web_evidence_manifest_sha256"] != event[
        "web_evidence_manifest_sha256"
    ]:
        raise IdentityWebEvidenceError("EVIDENCE_MANIFEST_HASH_CHANGED")
    if event["human_decision"] != "APPROVE":
        raise IdentityWebEvidenceError("HUMAN_APPROVAL_REQUIRED")
    _required_text(event["human_note"], "HUMAN_NOTE")
    _required_text(event["reviewed_at"], "REVIEWED_AT")


def expand_group_event(
    packet,
    evidence_manifest,
    groups,
    event,
    *,
    group_authority=None,
    association_authority=None,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    _validate_authority_event_common(event, packet, evidence_manifest)
    if group_authority is not None:
        recomputed_groups = recompute_groups(
            packet,
            evidence_manifest,
            group_authority,
            association_authority=association_authority,
        )
        if groups != recomputed_groups:
            raise IdentityWebEvidenceError("GROUP_PLAN_MISMATCH")
    group = next(
        (
            item for item in groups
            if item["candidate_group_id"] == event["candidate_group_id"]
        ),
        None,
    )
    if group is None:
        raise IdentityWebEvidenceError("GROUP_UNKNOWN")
    if group["member_review_ids"] != sorted(set(event["member_review_ids"])):
        raise IdentityWebEvidenceError("GROUP_MEMBERSHIP_MISMATCH")
    if group["evidence_class"] not in {
        "W1_OFFICIAL_PERSON_ID_EXACT",
        "W2_OFFICIAL_BIO_MULTI_SOURCE",
    }:
        raise IdentityWebEvidenceError("GROUP_NOT_ELIGIBLE")
    validate_player_uid(event["approved_player_uid"])
    _required_text(event["approved_canonical_name"], "APPROVED_CANONICAL_NAME")
    _required_text(event["allocation_attestation"], "ALLOCATION_ATTESTATION")
    review_by_id = {item["review_id"]: item for item in packet["reviews"]}
    decisions = []
    for review_id in group["member_review_ids"]:
        item = review_by_id[review_id]
        decisions.append({
            **item,
            "human_decision": "APPROVE",
            "human_note": (
                f"{event['human_note']} "
                f"[authority_event_id={event['event_id']}]"
            ),
            "approved_player_uid": event["approved_player_uid"],
            "approved_canonical_name": event["approved_canonical_name"],
            "source_exception_reason": None,
            "reviewed_at": event["reviewed_at"],
            "authority_event_id": event["event_id"],
        })
    return decisions


def expand_batch_event(
    packet,
    evidence_manifest,
    batches,
    event,
    *,
    search_statuses=None,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    _validate_authority_event_common(event, packet, evidence_manifest)
    if search_statuses is not None:
        expected_batches = recompute_batches(
            packet,
            evidence_manifest,
            search_statuses,
        )
        if batches != expected_batches:
            raise IdentityWebEvidenceError("BATCH_PLAN_MISMATCH")
    batch = next(
        (
            item for item in batches
            if item["batch_id"] == event["batch_id"]
        ),
        None,
    )
    if batch is None:
        raise IdentityWebEvidenceError("BATCH_UNKNOWN")
    if batch["member_review_ids"] != sorted(set(event["member_review_ids"])):
        raise IdentityWebEvidenceError("BATCH_MEMBERSHIP_MISMATCH")
    if batch["batch_predicate"] not in BATCH_PREDICATES:
        raise IdentityWebEvidenceError("BATCH_PREDICATE_INVALID")
    review_by_id = {item["review_id"]: item for item in packet["reviews"]}
    decisions = []
    for review_id in batch["member_review_ids"]:
        item = review_by_id[review_id]
        if item["proposal_type"] == "NEW_IDENTITY_CANDIDATE":
            raise IdentityWebEvidenceError("NEW_IDENTITY_BATCH_FORBIDDEN")
        decisions.append({
            **item,
            "human_decision": "APPROVE",
            "human_note": (
                f"{event['human_note']} "
                f"[authority_event_id={event['event_id']}]"
            ),
            "approved_player_uid": None,
            "approved_canonical_name": None,
            "source_exception_reason": None,
            "reviewed_at": event["reviewed_at"],
            "authority_event_id": event["event_id"],
        })
    return decisions


def _write_sheet(sheet, headers, rows, machine_fields=()):
    sheet.append(headers)
    for row in rows:
        sheet.append([
            (
                json.dumps(
                    row.get(header),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if isinstance(row.get(header), (list, dict))
                else row.get(header)
            )
            for header in headers
        ])
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            header = headers[cell.column - 1]
            if header in machine_fields:
                cell.protection = Protection(locked=True)
            else:
                cell.protection = Protection(locked=False)
    sheet.protection.sheet = True


def export_review_workbook(
    path,
    *,
    packet,
    evidence_manifest,
    groups,
    batches,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    workbook = Workbook()
    workbook.remove(workbook.active)
    review = workbook.create_sheet("Review")
    _write_sheet(
        review,
        list(REVIEW_FIELDS),
        packet["reviews"],
        machine_fields=MACHINE_REVIEW_FIELDS,
    )
    groups_sheet = workbook.create_sheet("Groups")
    groups_plan_sha = plan_sha256(groups)
    _write_sheet(
        groups_sheet,
        [
            "candidate_group_id",
            "member_review_ids",
            "member_count",
            "evidence_class",
            "conflict_count",
            "human_decision",
            "human_note",
            "approved_player_uid",
            "approved_canonical_name",
            "allocation_attestation",
            "reviewed_at",
            "plan_sha256",
        ],
        [
            {
                **group,
                "plan_sha256": groups_plan_sha,
                "member_review_ids": json.dumps(
                    group["member_review_ids"],
                    separators=(",", ":"),
                ),
            }
            for group in groups
        ],
        machine_fields={
            "candidate_group_id",
            "member_review_ids",
            "member_count",
            "evidence_class",
            "conflict_count",
            "plan_sha256",
        },
    )
    batches_sheet = workbook.create_sheet("Batches")
    batches_plan_sha = plan_sha256(batches)
    _write_sheet(
        batches_sheet,
        [
            "batch_id",
            "batch_predicate",
            "member_review_ids",
            "member_count",
            "human_decision",
            "human_note",
            "reviewed_at",
            "plan_sha256",
        ],
        [
            {
                **batch,
                "plan_sha256": batches_plan_sha,
                "member_review_ids": json.dumps(
                    batch["member_review_ids"],
                    separators=(",", ":"),
                ),
            }
            for batch in batches
        ],
        machine_fields={
            "batch_id",
            "batch_predicate",
            "member_review_ids",
            "member_count",
            "plan_sha256",
        },
    )
    evidence_sheet = workbook.create_sheet("Evidence_Index")
    evidence_rows = [
        {
            "evidence_id": item["evidence_id"],
            "source_tier": item["source_tier"],
            "source_url": item["source_url"],
            "source_kind": item["source_kind"],
            "content_sha256": item["content_sha256"],
        }
        for item in evidence_manifest["items"]
    ]
    _write_sheet(
        evidence_sheet,
        [
            "evidence_id",
            "source_tier",
            "source_url",
            "source_kind",
            "content_sha256",
        ],
        evidence_rows,
        machine_fields={
            "evidence_id",
            "source_tier",
            "source_url",
            "source_kind",
            "content_sha256",
        },
    )
    instructions = workbook.create_sheet("Instructions")
    for row in INSTRUCTIONS_ROWS:
        instructions.append(list(row))
    instructions.protection.sheet = True
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _sheet_table(path, sheet_name):
    workbook = load_workbook(path, read_only=False)
    sheet = workbook[sheet_name]
    values = list(sheet.values)
    headers = list(values[0])
    rows = [
        dict(zip(headers, row))
        for row in values[1:]
        if any(value is not None for value in row)
    ]
    return headers, rows


def _sheet_rows(path, sheet_name):
    return _sheet_table(path, sheet_name)[1]


def _verify_workbook_tables(path, *, packet, evidence_manifest, groups, batches):
    workbook = load_workbook(path, read_only=False)
    if tuple(workbook.sheetnames) != WORKBOOK_SHEETS:
        raise IdentityWebEvidenceError("WORKBOOK_SHEET_SET_INVALID")
    review_headers, review_rows = _sheet_table(path, "Review")
    if review_headers != list(REVIEW_FIELDS):
        raise IdentityWebEvidenceError("WORKBOOK_REVIEW_HEADERS_INVALID")
    group_headers, group_rows = _sheet_table(path, "Groups")
    expected_group_headers = [
        "candidate_group_id",
        "member_review_ids",
        "member_count",
        "evidence_class",
        "conflict_count",
        "human_decision",
        "human_note",
        "approved_player_uid",
        "approved_canonical_name",
        "allocation_attestation",
        "reviewed_at",
        "plan_sha256",
    ]
    if group_headers != expected_group_headers:
        raise IdentityWebEvidenceError("WORKBOOK_GROUP_HEADERS_INVALID")
    batch_headers, batch_rows = _sheet_table(path, "Batches")
    expected_batch_headers = [
        "batch_id",
        "batch_predicate",
        "member_review_ids",
        "member_count",
        "human_decision",
        "human_note",
        "reviewed_at",
        "plan_sha256",
    ]
    if batch_headers != expected_batch_headers:
        raise IdentityWebEvidenceError("WORKBOOK_BATCH_HEADERS_INVALID")
    evidence_headers, evidence_rows = _sheet_table(path, "Evidence_Index")
    expected_evidence_headers = [
        "evidence_id",
        "source_tier",
        "source_url",
        "source_kind",
        "content_sha256",
    ]
    if evidence_headers != expected_evidence_headers:
        raise IdentityWebEvidenceError("WORKBOOK_EVIDENCE_HEADERS_INVALID")
    instructions_headers, instructions_rows = _sheet_table(
        path,
        "Instructions",
    )
    if instructions_headers != ["Authority boundary"]:
        raise IdentityWebEvidenceError("WORKBOOK_INSTRUCTIONS_INVALID")
    actual_instructions = tuple(
        (row.get("Authority boundary"),)
        for row in instructions_rows
    )
    if actual_instructions != INSTRUCTIONS_ROWS[1:]:
        raise IdentityWebEvidenceError("WORKBOOK_INSTRUCTIONS_INVALID")

    group_by_id = {item["candidate_group_id"]: item for item in groups}
    expected_groups_plan_sha = plan_sha256(groups)
    for row in group_rows:
        expected = group_by_id.get(row["candidate_group_id"])
        if expected is None:
            raise IdentityWebEvidenceError("GROUP_UNKNOWN")
        for field in (
            "member_review_ids",
            "member_count",
            "evidence_class",
            "conflict_count",
        ):
            actual = row[field]
            if field == "member_review_ids" and isinstance(actual, str):
                actual = json.loads(actual)
            if actual != expected[field]:
                raise IdentityWebEvidenceError(
                    f"WORKBOOK_MACHINE_FIELD_TAMPER:Groups.{field}",
                )
        if row["plan_sha256"] != expected_groups_plan_sha:
            raise IdentityWebEvidenceError(
                "WORKBOOK_MACHINE_FIELD_TAMPER:Groups.plan_sha256",
            )

    batch_by_id = {item["batch_id"]: item for item in batches}
    expected_batches_plan_sha = plan_sha256(batches)
    for row in batch_rows:
        expected = batch_by_id.get(row["batch_id"])
        if expected is None:
            raise IdentityWebEvidenceError("BATCH_UNKNOWN")
        for field in ("batch_predicate", "member_review_ids", "member_count"):
            actual = row[field]
            if field == "member_review_ids" and isinstance(actual, str):
                actual = json.loads(actual)
            if actual != expected[field]:
                raise IdentityWebEvidenceError(
                    f"WORKBOOK_MACHINE_FIELD_TAMPER:Batches.{field}",
                )
        if row["plan_sha256"] != expected_batches_plan_sha:
            raise IdentityWebEvidenceError(
                "WORKBOOK_MACHINE_FIELD_TAMPER:Batches.plan_sha256",
            )

    expected_evidence = {
        item["evidence_id"]: {
            "source_tier": item["source_tier"],
            "source_url": item["source_url"],
            "source_kind": item["source_kind"],
            "content_sha256": item["content_sha256"],
        }
        for item in evidence_manifest["items"]
    }
    if len(evidence_rows) != len(expected_evidence):
        raise IdentityWebEvidenceError("WORKBOOK_EVIDENCE_ROWS_MISMATCH")
    for row in evidence_rows:
        expected = expected_evidence.get(row["evidence_id"])
        if expected is None:
            raise IdentityWebEvidenceError("WORKBOOK_EVIDENCE_UNKNOWN")
        for field, value in expected.items():
            if row[field] != value:
                raise IdentityWebEvidenceError(
                    f"WORKBOOK_MACHINE_FIELD_TAMPER:Evidence_Index.{field}",
                )


def import_review_workbook(
    path,
    *,
    packet,
    evidence_manifest,
    groups,
    batches,
    group_authority=None,
    association_authority=None,
    search_statuses=None,
):
    validate_review_packet(packet)
    validate_evidence_manifest(evidence_manifest)
    if groups and group_authority is None:
        raise IdentityWebEvidenceError("GROUP_AUTHORITY_REQUIRED")
    requires_search_authority = any(
        item["batch_predicate"] == "BATCH_NO_SAFE_SEARCH_EXHAUSTED"
        for item in batches
    )
    if requires_search_authority and search_statuses is None:
        raise IdentityWebEvidenceError("SEARCH_AUTHORITY_REQUIRED")
    if group_authority is not None:
        expected_groups = recompute_groups(
            packet,
            evidence_manifest,
            group_authority,
            association_authority=association_authority,
        )
        if groups != expected_groups:
            raise IdentityWebEvidenceError("GROUP_PLAN_MISMATCH")
    if search_statuses is not None:
        expected_batches = recompute_batches(
            packet,
            evidence_manifest,
            search_statuses,
        )
        if batches != expected_batches:
            raise IdentityWebEvidenceError("BATCH_PLAN_MISMATCH")
    _verify_workbook_tables(
        path,
        packet=packet,
        evidence_manifest=evidence_manifest,
        groups=groups,
        batches=batches,
    )
    review_rows = _sheet_rows(path, "Review")
    review_by_id = {item["review_id"]: item for item in packet["reviews"]}
    if {row["review_id"] for row in review_rows} != set(review_by_id):
        raise IdentityWebEvidenceError("WORKBOOK_REVIEW_ROWS_MISMATCH")
    decisions = {}
    for row in review_rows:
        authority = review_by_id[row["review_id"]]
        for field in MACHINE_REVIEW_FIELDS:
            actual = row.get(field)
            expected = authority[field]
            if field == "evidence_refs" and isinstance(actual, str):
                actual = json.loads(actual or "[]")
            if actual != expected:
                raise IdentityWebEvidenceError(
                    f"WORKBOOK_MACHINE_FIELD_TAMPER:{field}",
                )
        if row.get("human_decision"):
            decisions[row["review_id"]] = {
                **authority,
                "human_decision": row["human_decision"],
                "human_note": row.get("human_note"),
                "approved_player_uid": row.get("approved_player_uid"),
                "approved_canonical_name": row.get("approved_canonical_name"),
                "source_exception_reason": row.get("source_exception_reason"),
                "reviewed_at": row.get("reviewed_at"),
            }

    group_rows = _sheet_rows(path, "Groups")
    group_by_id = {item["candidate_group_id"]: item for item in groups}
    for row in group_rows:
        if not row.get("human_decision"):
            continue
        group_id = row["candidate_group_id"]
        expected = group_by_id.get(group_id)
        if expected is None:
            raise IdentityWebEvidenceError("GROUP_UNKNOWN")
        members = json.loads(row["member_review_ids"] or "[]")
        if members != expected["member_review_ids"]:
            raise IdentityWebEvidenceError("GROUP_MEMBERSHIP_MISMATCH")
        event = {
            "event_id": f"group-event-{group_id}",
            "candidate_group_id": group_id,
            "review_packet_sha256": packet["review_packet_sha256"],
            "web_evidence_manifest_sha256": evidence_manifest[
                "web_evidence_manifest_sha256"
            ],
            "member_review_ids": members,
            "human_decision": row["human_decision"],
            "human_note": row["human_note"],
            "approved_player_uid": row.get("approved_player_uid"),
            "approved_canonical_name": row.get("approved_canonical_name"),
            "allocation_attestation": row.get("allocation_attestation"),
            "reviewed_at": row["reviewed_at"],
        }
        for item in expand_group_event(
            packet,
            evidence_manifest,
            groups,
            event,
        ):
            decisions[item["review_id"]] = item

    batch_rows = _sheet_rows(path, "Batches")
    batch_by_id = {item["batch_id"]: item for item in batches}
    for row in batch_rows:
        if not row.get("human_decision"):
            continue
        batch_id = row["batch_id"]
        expected = batch_by_id.get(batch_id)
        if expected is None:
            raise IdentityWebEvidenceError("BATCH_UNKNOWN")
        members = json.loads(row["member_review_ids"] or "[]")
        if members != expected["member_review_ids"]:
            raise IdentityWebEvidenceError("BATCH_MEMBERSHIP_MISMATCH")
        event = {
            "event_id": f"batch-event-{batch_id}",
            "batch_id": batch_id,
            "review_packet_sha256": packet["review_packet_sha256"],
            "web_evidence_manifest_sha256": evidence_manifest[
                "web_evidence_manifest_sha256"
            ],
            "member_review_ids": members,
            "batch_predicate": row["batch_predicate"],
            "human_decision": row["human_decision"],
            "human_note": row["human_note"],
            "reviewed_at": row["reviewed_at"],
        }
        for item in expand_batch_event(
            packet,
            evidence_manifest,
            batches,
            event,
        ):
            if item["review_id"] in decisions:
                raise IdentityWebEvidenceError("WORKBOOK_DECISION_CONFLICT")
            decisions[item["review_id"]] = item

    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(REVIEW_FIELDS),
        lineterminator="\n",
    )
    writer.writeheader()
    for review_id in [item["review_id"] for item in packet["reviews"]]:
        item = decisions.get(review_id, review_by_id[review_id])
        writer.writerow({
            field: (
                json.dumps(
                    item[field],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if field == "evidence_refs"
                else ("" if item.get(field) is None else item.get(field))
            )
            for field in REVIEW_FIELDS
        })
    return output.getvalue().encode("utf-8")
