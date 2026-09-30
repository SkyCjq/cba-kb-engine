"""Research View v0: Read-only derived multi-grain research synthesis.

Follows REQ-190-STATEMENT-CLAIM-01 Deliverable D06:
Combines references to:
- Canonical Facts (MASTER registrations, facts)
- Documents (doc_id, metadata, rights, source_locator)
- Statements (statement_id, speaker, subjects, text, attribution_type, status)
- Claims (claim_id, text, status, supporting_statements)
- Unknown / Verification Queue (open verification items)
- Actor/Identity state (player registry status, resolved vs unresolved)

Invariants:
- The view labels each semantic grain explicitly.
- It is read-only and derived; it does NOT mutate upstream facts, documents,
  registrations, or identity decisions.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .actor import validate_actor_ref
from .claim import validate_claim
from .evidence_ledger import canonical_bytes
from .statement import validate_statement
from .verification_queue import validate_verification_item

RESEARCH_VIEW_VERSION = "v0"

GRAIN_LABELS = frozenset({
    "CANONICAL_FACT",
    "DOCUMENT",
    "STATEMENT",
    "CLAIM",
    "VERIFICATION_QUEUE",
    "ACTOR_IDENTITY",
})


class ResearchViewError(RuntimeError):
    pass


class ResearchView:
    """Read-only research view container."""

    def __init__(
        self,
        *,
        subject_name: Optional[str] = None,
        subject_player_uid: Optional[str] = None,
        canonical_facts: Optional[List[Dict[str, Any]]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        statements: Optional[List[Dict[str, Any]]] = None,
        claims: Optional[List[Dict[str, Any]]] = None,
        verification_items: Optional[List[Dict[str, Any]]] = None,
        actor_identity_states: Optional[List[Dict[str, Any]]] = None,
    ):
        self.subject_name = subject_name
        self.subject_player_uid = subject_player_uid
        self.canonical_facts = [
            {"semantic_grain": "CANONICAL_FACT", **f}
            for f in (canonical_facts or [])
        ]
        self.documents = [
            {"semantic_grain": "DOCUMENT", **d}
            for d in (documents or [])
        ]
        self.statements = [
            {"semantic_grain": "STATEMENT", **validate_statement(s)}
            for s in (statements or [])
        ]
        self.claims = [
            {"semantic_grain": "CLAIM", **validate_claim(c)}
            for c in (claims or [])
        ]
        self.verification_items = [
            {"semantic_grain": "VERIFICATION_QUEUE", **validate_verification_item(v)}
            for v in (verification_items or [])
        ]
        self.actor_identity_states = [
            {"semantic_grain": "ACTOR_IDENTITY", **a}
            for a in (actor_identity_states or [])
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "view_version": RESEARCH_VIEW_VERSION,
            "subject_name": self.subject_name,
            "subject_player_uid": self.subject_player_uid,
            "grains": {
                "canonical_facts": self.canonical_facts,
                "documents": self.documents,
                "statements": self.statements,
                "claims": self.claims,
                "verification_items": self.verification_items,
                "actor_identity_states": self.actor_identity_states,
            },
            "summary_counts": {
                "canonical_facts_count": len(self.canonical_facts),
                "documents_count": len(self.documents),
                "statements_count": len(self.statements),
                "claims_count": len(self.claims),
                "open_verification_items_count": len([v for v in self.verification_items if v.get("status") == "open"]),
                "actor_identity_count": len(self.actor_identity_states),
            },
        }

    def render_markdown(self) -> str:
        """Render a readable Markdown representation separating all grains."""
        lines: List[str] = [
            f"# 研究视图 (Research View v0): {self.subject_name or self.subject_player_uid or 'General'}",
            "",
            "> 声明：本视图为只读派生视图，严格区分事实层、文档层、发言层、主张层及待核项。",
            "",
            "## 1. 事实层 (CANONICAL_FACTS)",
        ]

        if not self.canonical_facts:
            lines.append("_暂无关联权威事实记录_")
        else:
            lines.append("| 赛季 | 俱乐部 | 注册类型 | 合同类别 |")
            lines.append("|---|---|---|---|")
            for f in self.canonical_facts:
                lines.append(f"| {f.get('season', '-')} | {f.get('club', '-')} | {f.get('registration_type', '-')} | {f.get('contract_type', '-')} |")

        lines.extend([
            "",
            "## 2. 身份与演员状态 (ACTOR_IDENTITY)",
        ])
        if not self.actor_identity_states:
            lines.append("_暂无特定演员状态记录_")
        else:
            for a in self.actor_identity_states:
                lines.append(f"- **{a.get('raw_name')}** ({a.get('kind')}): id={a.get('id') or 'null'} | 状态: {a.get('status', 'ACTIVE')}")

        lines.extend([
            "",
            "## 3. 发言与陈述层 (STATEMENTS)",
        ])
        if not self.statements:
            lines.append("_暂无提取的发言记录_")
        else:
            for s in self.statements:
                spk = s["speaker_actor_ref"]["raw_name"]
                spk_id = s["speaker_actor_ref"]["id"] or "unresolved"
                lines.append(f"- **[{s['statement_id']}]** ({s['attribution_type']}) [{spk} (id={spk_id})]: “{s['statement_text_or_controlled_excerpt']}” (状态: `{s['extraction_status']}`) [来源: `{s['source_ref']}`]")

        lines.extend([
            "",
            "## 4. 观点与主张层 (CLAIMS)",
        ])
        if not self.claims:
            lines.append("_暂无观点/主张记录_")
        else:
            for c in self.claims:
                lines.append(f"- **[{c['claim_id']}]** (`{c['status']}`): {c['claim_text']} (支撑发言: {c['supporting_statement_ids']})")

        lines.extend([
            "",
            "## 5. 待核队列与未决信号 (VERIFICATION_QUEUE)",
        ])
        open_items = [v for v in self.verification_items if v.get("status") == "open"]
        if not open_items:
            lines.append("_当前无未决待核项_")
        else:
            for v in open_items:
                lines.append(f"- **[{v['verification_id']}]** `{v['reason_code']}` ({v['object_type']}): 目标={v['object_ref']} | 证据={v['evidence_refs']}")

        lines.extend([
            "",
            "## 6. 证据文档来源 (DOCUMENTS)",
        ])
        if not self.documents:
            lines.append("_暂无引用文档_")
        else:
            for d in self.documents:
                lines.append(f"- **{d.get('doc_id')}**: {d.get('title', 'Untitled')} (来源: `{d.get('source_locator', '-')}`) | 权利: `{d.get('rights', {}).get('classification', 'unknown')}`")

        return "\n".join(lines) + "\n"
