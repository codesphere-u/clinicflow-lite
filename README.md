# clinicflow-lite

**Offline-first rural clinic WHO IMCI deterministic pediatric triage engine, WHO PQS vaccine cold-chain freeze/heat excursion & Arrhenius VVM analyzer, and FHIR R4 SQLite LWW sync node.**

Zero runtime dependencies. Pure Python 3.11+. Designed for low-resource Level II/III rural health centers, solar vaccine fridges, and community health workers operating across intermittent power and 2G/3G networks.

## Capabilities

- **WHO IMCI Pediatric Triage (`clinicflow_lite.imci`):** Deterministic 2–59 month rules for General Danger Signs, age-stratified respiratory rates (`<12m: >=50 bpm`, `12–59m: >=40 bpm`), dehydration plans (A/B/C), fever/malaria/stiff-neck red flags, and MUAC/edema malnutrition screening with transparent rule traces.
- **WHO PQS Vaccine Cold-Chain Guard (`clinicflow_lite.coldchain`):** Detects continuous freeze alarms (`<=-0.5°C` for `>=60 min`), continuous heat alarms (`>8°C` for `>=10 h`), and computes Arrhenius kinetic cumulative heat degradation across `VVM2`, `VVM7`, `VVM14`, and `VVM30` vaccine vial monitors.
- **FHIR R4 & Offline SQLite LWW Sync (`clinicflow_lite.fhir_sync`):** Emits standards-compliant FHIR R4 Bundles (`Patient`, `Encounter`, LOINC-coded `Observation`, `DetectedIssue`) backed by a WAL SQLite Last-Write-Wins sync queue with SHA-256 content digests.

> **Clinical Safety Disclaimer:** Decision-support tool aligned with WHO IMCI guidelines for trained health workers. Does NOT replace licensed clinical judgment or emergency medical care.

## Quick Start

```bash
uv sync --extra dev
clinicflow-lite demo
uv run pytest -v
```

## License

MIT License — see [LICENSE](LICENSE).
