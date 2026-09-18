# REQ-180-IDENTITY-COVERAGE-WEB-EVIDENCE-01 Task Brief

Freeze an official-web evidence enrichment and scalable human-review layer for
`REQ-180-IDENTITY-COVERAGE-01`.

Current review cost is not operationally acceptable:

- MASTER keys: `3465`
- already resolved same: `112`
- existing identity candidates: `40`
- no-safe candidates: `3313`
- current review rows: `3353`

The new layer must reduce manual research work without allowing web search,
names, team continuity, fuzzy matching, or LLM judgment to create `same`
automatically.

The r1 source policy is official-first:

- A0: Chinese Basketball Association registration/public notices
  (`cba.net.cn`, `cbanetcdn.cba.net.cn`)
- A1: CBA League registration/public notices
  (`cbaleague.com`, `image.cbaleague.com`)
- A2: official CBA player/data entities (`cbaleague.com`)
- B0: human-curated official club domains only
- D0: search engines/media/encyclopedias are discovery-only

Every accepted official resource is fetched read-only, snapshotted privately,
hashed, and represented through structured evidence claims.

Candidate evidence classes are frozen as W1 official person-ID exact, W2
official multi-source bio match, W3 official continuity, W4 discovery support,
and WX conflict. None is final identity authority.

The human-review contract is extended with explicit group/batch approval.
One human event may cover an exact frozen member set, but the system must
expand that event back into row-level reviewed decisions. New-identity groups
still require a human-supplied opaque UID, canonical name, evidence, and UID
allocation attestation. Unlisted group members remain unreviewed.

To avoid CSV/Excel encoding problems, add a non-canonical
`review_packet_workbook.xlsx` using the existing `openpyxl` dependency.
Machine-owned cells are verified on import; only human-owned decisions are
projected back into the existing UTF-8 validation flow.

The current `20260918T045146Z` review universe remains immutable audit history.
After implementation and private acceptance, regenerate a new web-enriched
review universe from the verified MASTER and canonical registry.

No new dependency, no Identity v1.8 core change, no direct canonical registry
write, no production access, and no real private mappings in Git are allowed.
