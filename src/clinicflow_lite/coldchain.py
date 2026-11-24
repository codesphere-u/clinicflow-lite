"""WHO PQS Vaccine Cold-Chain Excursion Analyzer & Arrhenius VVM Potency Estimator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# WHO EPI vaccine cold-chain safe window (+2.0 C to +8.0 C)
WHO_SAFE_MIN_C = 2.0
WHO_SAFE_MAX_C = 8.0
# WHO PQS E006/TR06.3 continuous alarm thresholds
WHO_FREEZE_ALARM_C = -0.5
WHO_FREEZE_ALARM_MINUTES = 60.0
WHO_HEAT_ALARM_C = 8.0
WHO_HEAT_ALARM_HOURS = 10.0

# Arrhenius gas constant (J / mol*K) and representative VVM14 activation energy (~85 kJ/mol)
_GAS_CONSTANT_R = 8.314462618
_DEFAULT_EA_J_MOL = 85_000.0
# Reference: VVM14 reaches 100% endpoint after 14 days (336 hours) at 37 C (310.15 K)
_REF_TEMP_K = 310.15
_VVM_ENDPOINT_HOURS_AT_37C: dict[str, float] = {
    "VVM2": 48.0,
    "VVM7": 168.0,
    "VVM14": 336.0,
    "VVM30": 720.0,
}


class VvmStage(StrEnum):
    STAGE_1_GOOD = "stage_1_good"
    STAGE_2_USE_FIRST = "stage_2_use_first"
    STAGE_3_DISCARD = "stage_3_discard"
    STAGE_4_DISCARD = "stage_4_discard"


@dataclass(frozen=True)
class TempReading:
    timestamp_iso: str
    temp_c: float


@dataclass(frozen=True)
class ColdChainReport:
    readings_count: int
    duration_hours: float
    min_temp_c: float
    max_temp_c: float
    max_continuous_freeze_minutes: float
    max_continuous_heat_hours: float
    freeze_alarm_triggered: bool
    heat_alarm_triggered: bool
    vvm_type: str
    estimated_vvm_degradation_pct: float
    estimated_vvm_stage: VvmStage
    alerts: tuple[str, ...]
    safe_for_use: bool


def analyze_cold_chain(
    readings: list[TempReading],
    *,
    vvm_type: str = "VVM14",
    freeze_sensitive: bool = True,
) -> ColdChainReport:
    """Analyze timestamped fridge/cold-box telemetry against WHO PQS freeze/heat & Arrhenius VVM rules."""
    if len(readings) < 2:
        raise ValueError("At least 2 timestamped temperature readings are required")
    if vvm_type not in _VVM_ENDPOINT_HOURS_AT_37C:
        raise ValueError(f"Unsupported vvm_type '{vvm_type}'; expected {sorted(_VVM_ENDPOINT_HOURS_AT_37C)}")

    parsed = sorted(
        [(datetime.fromisoformat(r.timestamp_iso.replace("Z", "+00:00")), r.temp_c) for r in readings],
        key=lambda x: x[0],
    )

    min_c = min(t for _, t in parsed)
    max_c = max(t for _, t in parsed)
    total_hours = (parsed[-1][0] - parsed[0][0]).total_seconds() / 3600.0

    cur_freeze_min = 0.0
    max_freeze_min = 0.0
    cur_heat_hr = 0.0
    max_heat_hr = 0.0
    vvm_fraction_used = 0.0
    endpoint_hours = _VVM_ENDPOINT_HOURS_AT_37C[vvm_type]

    for i in range(len(parsed) - 1):
        t0, c0 = parsed[i]
        t1, c1 = parsed[i + 1]
        dt_sec = max(0.0, (t1 - t0).total_seconds())
        dt_min = dt_sec / 60.0
        dt_hr = dt_sec / 3600.0
        avg_c = 0.5 * (c0 + c1)

        if avg_c <= WHO_FREEZE_ALARM_C:
            cur_freeze_min += dt_min
            max_freeze_min = max(max_freeze_min, cur_freeze_min)
        else:
            cur_freeze_min = 0.0

        if avg_c > WHO_HEAT_ALARM_C:
            cur_heat_hr += dt_hr
            max_heat_hr = max(max_heat_hr, cur_heat_hr)
        else:
            cur_heat_hr = 0.0

        # Arrhenius thermal degradation rate relative to 37 C endpoint
        temp_k = max(220.0, avg_c + 273.15)
        rate_ratio = math.exp((_DEFAULT_EA_J_MOL / _GAS_CONSTANT_R) * ((1.0 / _REF_TEMP_K) - (1.0 / temp_k)))
        vvm_fraction_used += (dt_hr / endpoint_hours) * rate_ratio

    freeze_alarm = freeze_sensitive and (max_freeze_min >= WHO_FREEZE_ALARM_MINUTES)
    heat_alarm = max_heat_hr >= WHO_HEAT_ALARM_HOURS
    degradation_pct = min(100.0, round(vvm_fraction_used * 100.0, 2))

    if degradation_pct < 25.0:
        stage = VvmStage.STAGE_1_GOOD
    elif degradation_pct < 100.0:
        stage = VvmStage.STAGE_2_USE_FIRST
    else:
        stage = VvmStage.STAGE_4_DISCARD

    alerts: list[str] = []
    if freeze_alarm:
        alerts.append(
            f"WHO FREEZE ALARM: Continuous <= {WHO_FREEZE_ALARM_C}C for {max_freeze_min:.0f} min "
            "(perform WHO Shake Test / quarantine freeze-sensitive vials)."
        )
    if heat_alarm:
        alerts.append(
            f"WHO HEAT ALARM: Continuous > {WHO_HEAT_ALARM_C}C for {max_heat_hr:.1f} hrs "
            f"(inspect physical VVM; estimated {vvm_type} loss {degradation_pct}%)."
        )
    if stage in (VvmStage.STAGE_3_DISCARD, VvmStage.STAGE_4_DISCARD):
        alerts.append(f"VVM ENDPOINT REACHED ({degradation_pct}%): Do NOT administer compromised vials.")

    safe = not freeze_alarm and not heat_alarm and stage not in (VvmStage.STAGE_3_DISCARD, VvmStage.STAGE_4_DISCARD)
    return ColdChainReport(
        readings_count=len(readings),
        duration_hours=round(total_hours, 2),
        min_temp_c=round(min_c, 2),
        max_temp_c=round(max_c, 2),
        max_continuous_freeze_minutes=round(max_freeze_min, 2),
        max_continuous_heat_hours=round(max_heat_hr, 2),
        freeze_alarm_triggered=freeze_alarm,
        heat_alarm_triggered=heat_alarm,
        vvm_type=vvm_type,
        estimated_vvm_degradation_pct=degradation_pct,
        estimated_vvm_stage=stage,
        alerts=tuple(alerts),
        safe_for_use=safe,
    )
