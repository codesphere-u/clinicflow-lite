"""Tests for WHO IMCI triage, WHO PQS cold-chain excursion + Arrhenius VVM, FHIR R4 sync & CLI."""

from __future__ import annotations

import json

import pytest

from clinicflow_lite import (
    ImciAssessmentInput,
    OfflineClinicStore,
    TempReading,
    TriagePriority,
    VvmStage,
    analyze_cold_chain,
    build_fhir_r4_bundle,
    classify_imci,
)
from clinicflow_lite.cli import main as cli_main


def test_imci_danger_sign_and_age_stratified_pneumonia_and_sam() -> None:
    # General danger sign forces RED urgent referral
    red = classify_imci(ImciAssessmentInput(age_months=8, convulsions=True, temperature_c=39.2))
    assert red.overall_priority == TriagePriority.RED
    assert red.urgent_referral_required is True

    # Age-stratified fast breathing: 45 bpm is NORMAL for 8-month-old (<50), FAST for 24-month-old (>=40)
    infant = classify_imci(
        ImciAssessmentInput(age_months=8, cough_or_difficult_breathing=True, respiratory_rate_bpm=45)
    )
    assert infant.overall_priority == TriagePriority.GREEN

    toddler = classify_imci(
        ImciAssessmentInput(age_months=24, cough_or_difficult_breathing=True, respiratory_rate_bpm=45)
    )
    assert toddler.overall_priority == TriagePriority.YELLOW
    assert any(c.label == "PNEUMONIA" for c in toddler.classifications)

    # Severe Acute Malnutrition (MUAC < 115mm) triggers RED
    sam = classify_imci(ImciAssessmentInput(age_months=18, muac_mm=110))
    assert sam.overall_priority == TriagePriority.RED
    assert any("SAM" in c.label for c in sam.classifications)

    with pytest.raises(ValueError):
        ImciAssessmentInput(age_months=1)


def test_cold_chain_safe_freeze_alarm_and_heat_vvm_degradation() -> None:
    safe_logs = [
        TempReading("2026-11-24T00:00:00Z", 4.0),
        TempReading("2026-11-24T06:00:00Z", 5.0),
        TempReading("2026-11-24T12:00:00Z", 4.5),
    ]
    ok = analyze_cold_chain(safe_logs, vvm_type="VVM14")
    assert ok.safe_for_use is True
    assert ok.freeze_alarm_triggered is False
    assert ok.heat_alarm_triggered is False
    assert ok.estimated_vvm_stage == VvmStage.STAGE_1_GOOD

    # Continuous freeze <= -0.5C for 90 minutes triggers WHO freeze alarm
    freeze_logs = [
        TempReading("2026-11-24T00:00:00Z", 3.0),
        TempReading("2026-11-24T01:00:00Z", -1.5),
        TempReading("2026-11-24T02:30:00Z", -1.2),
        TempReading("2026-11-24T03:00:00Z", 4.0),
    ]
    frozen = analyze_cold_chain(freeze_logs, freeze_sensitive=True)
    assert frozen.freeze_alarm_triggered is True
    assert frozen.safe_for_use is False
    assert any("FREEZE ALARM" in a for a in frozen.alerts)

    # Sustained 37C exposure for 60 hours exceeds VVM2 endpoint (48h at 37C)
    hot_logs = [
        TempReading("2026-11-20T00:00:00Z", 37.0),
        TempReading("2026-11-22T12:00:00Z", 37.0),
    ]
    cooked = analyze_cold_chain(hot_logs, vvm_type="VVM2")
    assert cooked.heat_alarm_triggered is True
    assert cooked.estimated_vvm_stage == VvmStage.STAGE_4_DISCARD
    assert cooked.safe_for_use is False


def test_fhir_r4_bundle_lww_sync_and_cli_demo(capsys: pytest.CaptureFixture[str]) -> None:
    inp = ImciAssessmentInput(
        age_months=14,
        diarrhea=True,
        sunken_eyes=True,
        skin_pinch_seconds=3.0,
        drinks_poorly=True,
        temperature_c=38.1,
    )
    res = classify_imci(inp)
    assert res.overall_priority == TriagePriority.RED
    bundle = build_fhir_r4_bundle("PAT-1", "KISORO-HC3", inp, res)
    assert bundle["resourceType"] == "Bundle"
    assert any(e["resource"]["resourceType"] == "DetectedIssue" for e in bundle["entry"])

    store = OfflineClinicStore(":memory:")
    assert store.upsert_lww("rec-1", "KISORO-HC3", bundle, updated_ms=2000) is True
    # Older out-of-order sync update must be ignored (LWW)
    assert store.upsert_lww("rec-1", "KISORO-HC3", bundle, updated_ms=1000) is False
    assert len(store.pending_sync()) == 1
    assert store.mark_synced(["rec-1"]) == 1
    assert len(store.pending_sync()) == 0
    store.close()

    assert cli_main(["demo"]) == 0
    demo = json.loads(capsys.readouterr().out)
    assert demo["cold_chain_safe"] is True
    assert demo["fhir_entries"] >= 5
