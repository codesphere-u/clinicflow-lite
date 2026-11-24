"""Deterministic WHO IMCI (2-59 months) pediatric clinical triage decision-support engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

CLINICAL_DISCLAIMER = (
    "Decision-support tool aligned with WHO IMCI guidelines for trained health workers. "
    "Does NOT replace licensed clinical judgment or emergency medical care."
)


class TriagePriority(StrEnum):
    RED = "red_urgent_referral"
    YELLOW = "yellow_outpatient_treatment"
    GREEN = "green_home_care"


@dataclass(frozen=True)
class ImciAssessmentInput:
    age_months: int
    unable_to_drink: bool = False
    vomits_everything: bool = False
    convulsions: bool = False
    lethargic_or_unconscious: bool = False
    cough_or_difficult_breathing: bool = False
    respiratory_rate_bpm: int | None = None
    chest_indrawing: bool = False
    stridor_in_calm_child: bool = False
    diarrhea: bool = False
    sunken_eyes: bool = False
    drinks_poorly: bool = False
    drinks_eagerly: bool = False
    skin_pinch_seconds: float = 0.0
    temperature_c: float | None = None
    stiff_neck: bool = False
    malaria_rdt_positive: bool = False
    muac_mm: int | None = None
    bilateral_pedal_edema: bool = False

    def __post_init__(self) -> None:
        if not 2 <= self.age_months <= 59:
            raise ValueError("WHO IMCI child chart covers ages 2 to 59 months")


@dataclass(frozen=True)
class ImciClassification:
    domain: str
    priority: TriagePriority
    label: str
    actions: tuple[str, ...]
    rule_trace: str


@dataclass(frozen=True)
class ImciTriageResult:
    overall_priority: TriagePriority
    urgent_referral_required: bool
    classifications: tuple[ImciClassification, ...]
    disclaimer: str = CLINICAL_DISCLAIMER


def classify_imci(inp: ImciAssessmentInput) -> ImciTriageResult:
    """Apply WHO IMCI deterministic rules across danger signs, respiratory, dehydration, fever, and MUAC."""
    classifications: list[ImciClassification] = []

    # 1. General Danger Signs
    danger_signs: list[str] = []
    if inp.unable_to_drink:
        danger_signs.append("unable_to_drink")
    if inp.vomits_everything:
        danger_signs.append("vomits_everything")
    if inp.convulsions:
        danger_signs.append("convulsions")
    if inp.lethargic_or_unconscious:
        danger_signs.append("lethargic_or_unconscious")

    if danger_signs:
        classifications.append(
            ImciClassification(
                domain="general_danger_signs",
                priority=TriagePriority.RED,
                label="GENERAL DANGER SIGN PRESENT",
                actions=(
                    "Complete assessment rapidly and give pre-referral stabilization.",
                    "Refer URGENTLY to hospital.",
                ),
                rule_trace=f"IMCI-GDS: triggered by {', '.join(danger_signs)}",
            )
        )

    # 2. Cough or Difficult Breathing (WHO age-stratified respiratory rate cutoff)
    if inp.cough_or_difficult_breathing:
        fast_cutoff = 50 if inp.age_months < 12 else 40
        fast_breathing = (
            inp.respiratory_rate_bpm is not None and inp.respiratory_rate_bpm >= fast_cutoff
        )
        if danger_signs or inp.stridor_in_calm_child:
            classifications.append(
                ImciClassification(
                    domain="respiratory",
                    priority=TriagePriority.RED,
                    label="SEVERE PNEUMONIA OR VERY SEVERE DISEASE",
                    actions=(
                        "Give first dose of appropriate antibiotic per national IMCI protocol.",
                        "Refer URGENTLY to hospital; monitor SpO2 if available.",
                    ),
                    rule_trace="IMCI-RESP-RED: danger sign or stridor in calm child",
                )
            )
        elif inp.chest_indrawing or fast_breathing:
            classifications.append(
                ImciClassification(
                    domain="respiratory",
                    priority=TriagePriority.YELLOW,
                    label="PNEUMONIA",
                    actions=(
                        "Treat with oral amoxicillin per age/weight IMCI dosing chart.",
                        "Advise caregiver on danger signs and 2-day follow-up.",
                    ),
                    rule_trace=(
                        f"IMCI-RESP-YEL: rr={inp.respiratory_rate_bpm} "
                        f"(cutoff>={fast_cutoff}) or chest_indrawing={inp.chest_indrawing}"
                    ),
                )
            )
        else:
            classifications.append(
                ImciClassification(
                    domain="respiratory",
                    priority=TriagePriority.GREEN,
                    label="COUGH OR COLD (NO PNEUMONIA)",
                    actions=(
                        "Soothe throat with safe home remedy.",
                        "Advise caregiver when to return immediately if breathing worsens.",
                    ),
                    rule_trace="IMCI-RESP-GRN: no fast breathing, indrawing, or stridor",
                )
            )

    # 3. Diarrhea & Dehydration
    if inp.diarrhea:
        severe_signs = sum(
            [
                inp.lethargic_or_unconscious,
                inp.sunken_eyes,
                inp.drinks_poorly or inp.unable_to_drink,
                inp.skin_pinch_seconds > 2.0,
            ]
        )
        some_signs = sum(
            [
                inp.sunken_eyes,
                inp.drinks_eagerly,
                0.5 <= inp.skin_pinch_seconds <= 2.0,
            ]
        )
        if severe_signs >= 2:
            classifications.append(
                ImciClassification(
                    domain="diarrhea",
                    priority=TriagePriority.RED,
                    label="SEVERE DEHYDRATION",
                    actions=(
                        "Initiate Plan C urgent IV/NG rehydration immediately.",
                        "Refer URGENTLY if IV therapy unavailable at clinic.",
                    ),
                    rule_trace=f"IMCI-DIAR-RED: {severe_signs} severe dehydration signs",
                )
            )
        elif some_signs >= 2:
            classifications.append(
                ImciClassification(
                    domain="diarrhea",
                    priority=TriagePriority.YELLOW,
                    label="SOME DEHYDRATION",
                    actions=(
                        "Give oral rehydration salts (ORS) under Plan B + zinc supplementation.",
                        "Reassess hydration after 4 hours.",
                    ),
                    rule_trace=f"IMCI-DIAR-YEL: {some_signs} moderate dehydration signs",
                )
            )
        else:
            classifications.append(
                ImciClassification(
                    domain="diarrhea",
                    priority=TriagePriority.GREEN,
                    label="NO DEHYDRATION",
                    actions=(
                        "Follow Plan A home fluids, continued feeding, and zinc.",
                        "Return immediately if unable to drink or blood in stool.",
                    ),
                    rule_trace="IMCI-DIAR-GRN: <2 dehydration indicators",
                )
            )

    # 4. Fever / Meningitis / Malaria
    has_fever = inp.temperature_c is not None and inp.temperature_c >= 37.5
    if has_fever or inp.stiff_neck or inp.malaria_rdt_positive:
        if inp.stiff_neck or danger_signs:
            classifications.append(
                ImciClassification(
                    domain="fever",
                    priority=TriagePriority.RED,
                    label="VERY SEVERE FEBRILE DISEASE",
                    actions=(
                        "Give pre-referral parenteral antimalarial/antibiotic per national guidelines.",
                        "Prevent hypoglycemia and refer URGENTLY to hospital.",
                    ),
                    rule_trace="IMCI-FEV-RED: fever with stiff neck or general danger sign",
                )
            )
        elif inp.malaria_rdt_positive:
            classifications.append(
                ImciClassification(
                    domain="fever",
                    priority=TriagePriority.YELLOW,
                    label="MALARIA (RDT POSITIVE)",
                    actions=(
                        "Treat with first-line ACT per weight/age chart.",
                        "Give paracetamol for fever >=38.5C and follow up in 2 days if fever persists.",
                    ),
                    rule_trace="IMCI-FEV-YEL: positive malaria RDT without danger signs",
                )
            )

    # 5. Acute Malnutrition (6-59 months MUAC / Edema)
    if inp.bilateral_pedal_edema or (inp.age_months >= 6 and inp.muac_mm is not None and inp.muac_mm < 115):
        classifications.append(
            ImciClassification(
                domain="nutrition",
                priority=TriagePriority.RED,
                label="SEVERE ACUTE MALNUTRITION (SAM)",
                actions=(
                    "Assess appetite & medical complications; enroll in inpatient/OTP therapeutic care.",
                    "Prevent hypoglycemia and hypothermia during transfer.",
                ),
                rule_trace=f"IMCI-NUT-RED: edema={inp.bilateral_pedal_edema}, muac_mm={inp.muac_mm}",
            )
        )
    elif inp.age_months >= 6 and inp.muac_mm is not None and 115 <= inp.muac_mm < 125:
        classifications.append(
            ImciClassification(
                domain="nutrition",
                priority=TriagePriority.YELLOW,
                label="MODERATE ACUTE MALNUTRITION (MAM)",
                actions=(
                    "Enroll in supplementary feeding program and provide caregiver nutrition counseling.",
                    "Schedule 14-day weight and MUAC follow-up.",
                ),
                rule_trace=f"IMCI-NUT-YEL: muac_mm={inp.muac_mm} (115-124mm)",
            )
        )

    if not classifications:
        classifications.append(
            ImciClassification(
                domain="general",
                priority=TriagePriority.GREEN,
                label="NO ACUTE IMCI DANGER SIGNS DETECTED",
                actions=(
                    "Verify immunization schedule, vitamin A, and routine growth monitoring.",
                    "Advise caregiver on danger signs requiring immediate clinic return.",
                ),
                rule_trace="IMCI-GEN-GRN: no red/yellow IMCI criteria matched",
            )
        )

    priorities = {c.priority for c in classifications}
    if TriagePriority.RED in priorities:
        overall = TriagePriority.RED
    elif TriagePriority.YELLOW in priorities:
        overall = TriagePriority.YELLOW
    else:
        overall = TriagePriority.GREEN

    return ImciTriageResult(
        overall_priority=overall,
        urgent_referral_required=(overall == TriagePriority.RED),
        classifications=tuple(classifications),
    )
