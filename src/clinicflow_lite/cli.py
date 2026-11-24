"""Command-line interface for clinicflow-lite."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from clinicflow_lite.coldchain import TempReading, analyze_cold_chain
from clinicflow_lite.fhir_sync import OfflineClinicStore, build_fhir_r4_bundle
from clinicflow_lite.imci import ImciAssessmentInput, classify_imci


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clinicflow-lite",
        description="Offline-first WHO IMCI triage, vaccine cold-chain excursion guard & FHIR R4 sync CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run an offline rural clinic IMCI + cold-chain + FHIR R4 demo")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        assessment = ImciAssessmentInput(
            age_months=10,
            cough_or_difficult_breathing=True,
            respiratory_rate_bpm=56,
            temperature_c=38.6,
            malaria_rdt_positive=True,
            muac_mm=120,
        )
        triage = classify_imci(assessment)
        bundle = build_fhir_r4_bundle("PAT-UG-01", "GULU-HC3", assessment, triage)
        store = OfflineClinicStore(":memory:")
        store.upsert_lww("PAT-UG-01-ENC1", "GULU-HC3", bundle, updated_ms=1764000000000)

        cc = analyze_cold_chain(
            [
                TempReading("2026-11-24T00:00:00Z", 4.5),
                TempReading("2026-11-24T04:00:00Z", 5.2),
                TempReading("2026-11-24T08:00:00Z", 4.8),
            ]
        )
        print(
            json.dumps(
                {
                    "imci_priority": triage.overall_priority.value,
                    "classifications": [c.label for c in triage.classifications],
                    "fhir_entries": len(bundle["entry"]),
                    "pending_sync": len(store.pending_sync()),
                    "cold_chain_safe": cc.safe_for_use,
                    "vvm_stage": cc.estimated_vvm_stage.value,
                },
                indent=2,
            )
        )
        store.close()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
