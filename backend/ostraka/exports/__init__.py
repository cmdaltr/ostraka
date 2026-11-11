"""Exports for other tools: a STIX 2.1 bundle, a MISP event and an ATT&CK Navigator layer.

`record.gather` reads the incident once into plain values (`IntelRecord`); `stix`, `misp` and
`navigator` turn that into each format with pure functions, so the formats are tested without a
database. The API is `api/exports.py`.

Rules every format keeps:
- Leads are never presented as confirmed. A finding is confirmed only when an analyst confirmed it;
  an indicator counts as confirmed only when its verdict is malicious and a confirmed finding cites
  it. Everything else is a lead and says so (labels, confidence, MISP's to_ids).
- Refuted findings and benign observables are left out.
- Indicator values go out as they are, typed, because that's what the formats are for. Every piece
  of prose (titles, statements, notes) is masked with `mask_text`, as anything leaving ostraka is,
  so an indicator never travels outside its typed, TLP-marked place.
- Who may export, and the TLP:RED / TLP:AMBER+STRICT rule, is `policy.can_export_intel`.
"""
