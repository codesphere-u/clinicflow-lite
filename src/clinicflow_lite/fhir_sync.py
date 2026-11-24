"""Offline-first FHIR R4 Bundle generator and SQLite LWW sync outbox for rural clinics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clinicflow_lite.imci import ImciAssessmentInput, ImciTriageResult


def build_fhir_r4_bundle(
    patient_id: str,
    clinic_id: str,
    assessment: ImciAssessmentInput,
    triage: ImciTriageResult,
    *,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Export an IMCI encounter and clinical observations as a standards-compliant FHIR R4 Bundle."""
    ts = (recorded_at or datetime.now(UTC)).isoformat()
    enc_id = f"enc-{uuid.uuid4().hex[:10]}"
    entries: list[dict[str, Any]] = [
        {
            "resource": {
                "resourceType": "Patient",
                "id": patient_id,
                "active": True,
                "extension": [
                    {
                        "url": "http://codesphere-u.org/fhir/StructureDefinition/age-months",
                        "valueInteger": assessment.age_months,
                    }
                ],
            }
        },
        {
            "resource": {
                "resourceType": "Encounter",
                "id": enc_id,
                "status": "finished",
                "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
                "subject": {"reference": f"Patient/{patient_id}"},
                "serviceProvider": {"display": clinic_id},
                "period": {"start": ts, "end": ts},
                "priority": {"text": triage.overall_priority.value},
            }
        },
    ]

    if assessment.temperature_c is not None:
        entries.append(
            {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {
                        "coding": [
                            {"system": "http://loinc.org", "code": "8310-5", "display": "Body temperature"}
                        ]
                    },
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "encounter": {"reference": f"Encounter/{enc_id}"},
                    "valueQuantity": {"value": assessment.temperature_c, "unit": "Cel"},
                }
            }
        )
    if assessment.respiratory_rate_bpm is not None:
        entries.append(
            {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {
                        "coding": [
                            {"system": "http://loinc.org", "code": "9279-1", "display": "Respiratory rate"}
                        ]
                    },
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "encounter": {"reference": f"Encounter/{enc_id}"},
                    "valueQuantity": {"value": assessment.respiratory_rate_bpm, "unit": "/min"},
                }
            }
        )
    if assessment.muac_mm is not None:
        entries.append(
            {
                "resource": {
                    "resourceType": "Observation",
                    "status": "final",
                    "code": {"coding": [{"system": "http://loinc.org", "code": "56072-2", "display": "MUAC"}]},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "encounter": {"reference": f"Encounter/{enc_id}"},
                    "valueQuantity": {"value": assessment.muac_mm, "unit": "mm"},
                }
            }
        )

    for c in triage.classifications:
        sev = "high" if c.priority.name == "RED" else "moderate" if c.priority.name == "YELLOW" else "low"
        entries.append(
            {
                "resource": {
                    "resourceType": "DetectedIssue",
                    "status": "final",
                    "severity": sev,
                    "patient": {"reference": f"Patient/{patient_id}"},
                    "detail": f"[{c.domain}] {c.label} ({c.rule_trace})",
                }
            }
        )

    return {
        "resourceType": "Bundle",
        "id": f"bundle-{uuid.uuid4().hex[:12]}",
        "type": "collection",
        "timestamp": ts,
        "entry": entries,
    }


class OfflineClinicStore:
    """SQLite WAL offline-first store with SHA-256 integrity and LWW conflict-safe FHIR sync."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS fhir_records (
                record_id TEXT PRIMARY KEY,
                updated_ms INTEGER NOT NULL,
                clinic_id TEXT NOT NULL,
                bundle_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                synced INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def upsert_lww(
        self,
        record_id: str,
        clinic_id: str,
        bundle: dict[str, Any],
        *,
        updated_ms: int,
    ) -> bool:
        """Apply Last-Write-Wins merge by (updated_ms, clinic_id); returns True if state changed."""
        canonical = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing = self._conn.execute(
            "SELECT updated_ms, clinic_id FROM fhir_records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if existing is not None:
            existing_key = (int(existing["updated_ms"]), str(existing["clinic_id"]))
            if existing_key >= (updated_ms, clinic_id):
                return False

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO fhir_records (record_id, updated_ms, clinic_id, bundle_json, content_sha256, synced)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(record_id) DO UPDATE SET
                    updated_ms=excluded.updated_ms,
                    clinic_id=excluded.clinic_id,
                    bundle_json=excluded.bundle_json,
                    content_sha256=excluded.content_sha256,
                    synced=0
                """,
                (record_id, updated_ms, clinic_id, canonical, digest),
            )
        return True

    def pending_sync(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT record_id, updated_ms, clinic_id, bundle_json, content_sha256
            FROM fhir_records WHERE synced = 0 ORDER BY updated_ms ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_synced(self, record_ids: list[str]) -> int:
        if not record_ids:
            return 0
        placeholders = ",".join("?" for _ in record_ids)
        with self._conn:
            cur = self._conn.execute(
                f"UPDATE fhir_records SET synced = 1 WHERE record_id IN ({placeholders})",
                record_ids,
            )
        return cur.rowcount
