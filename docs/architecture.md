# Architecture — `clinicflow-lite`

- `clinicflow_lite.imci`: Pure-Python deterministic WHO IMCI classification tree (2–59 months).
- `clinicflow_lite.coldchain`: WHO PQS freeze/heat duration tracker + Arrhenius kinetic VVM degradation estimator.
- `clinicflow_lite.fhir_sync`: FHIR R4 JSON Bundle serializer and SQLite WAL LWW offline sync outbox.
- `clinicflow_lite.cli`: Zero-ops operator CLI (`demo`).
