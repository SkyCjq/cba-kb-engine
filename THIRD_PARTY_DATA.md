# Third-Party Data & Redistribution Boundaries

This document details the boundaries, attributions, and restrictions governing third-party materials and primary sources referenced by or processed through the CBA-KB Engine.

## 1. Upstream Primary Sources

CBA-KB research pipelines ingest public announcements, league registration tables, club notices, news reports, and historical match records.
- **Copyright Ownership**: Upstream documents, images, and public notices remain the intellectual property of their original authors, publishers, the Chinese Basketball Association (CBA League), and respective member clubs.
- **Engine Position**: This public repository does **not** host, redistribute, or license bulk copyrighted corpora. The Engine only provides reproducible parsers, schemas, and validators.

## 2. Fair Use and Fact Extraction

- Under applicable copyright laws, pure facts, registration dates, player statistics, and historical events are generally not copyrightable subject matter.
- Direct verbatim expressions, editorial analyses, and original prose belong to their original publishers.
- Extracted statements maintain explicit source references and provenance tracking to respect attribution requirements and avoid ungrounded claim synthesis.

## 3. Boundary Rules for Downstream Users and Operators

1. **Verify Upstream Rights**: If you operate a Private Instance to ingest real-world documents, you are solely responsible for ensuring your data collection and distribution comply with applicable copyright laws and terms of service.
2. **Respect Rights Classifications**: When generating consumer outputs using `cba-kb consumer-build` or research views, always heed the underlying rights classifications (`PUBLIC_SAFE`, `RESEARCH_ONLY`, `RESTRICTED_INTERNAL`).
3. **No Endorsement**: Reference to any third-party names, trademarks, clubs, or entities does not constitute endorsement, sponsorship, or affiliation with the CBA-KB project.
