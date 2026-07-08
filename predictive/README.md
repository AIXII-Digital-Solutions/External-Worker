# Forecast engine — airline activity, 24 months ahead (External-Worker)

External estimate of a carrier's **business** (network, flight volumes, fleet utilisation) 24 months
ahead, monthly, from public data (FR24 + Cirium). Design is frozen (21 nodes) — see
`Core-API/docs/airline forecast handoff prompt.md` (step 1) and `handoff_v2_thin_slice_to_loco.md`
(step 2, current). **Build, don't re-open** the design; raise only *real* data-driven contradictions.

Core premise (a CHOICE, not proven): "fixed observed fleet under the Cirium `Operator` key on a frozen
network." The single judge is **LOCO coverage** — a miss means revisit the *assumption*, not the code.

## Where the pieces live
- **Historical data:** `forecast.acys_actuals` (Cirium × FR24, date-respecting per-month operator join;
  one row per flight). Assembled by the `forecast_panel` job (`worker/API/ForecastAPI/panel.py`).
- **Archetype (step 1, DONE):** `Core-API/predictive/archetype/` — S1–S4 signatures + PCA. Verdict:
  **discrete** schedule vs on-demand buckets on PC1 (60.7% var, bimodal, antimode ≈ −0.30); PC2 is a
  weekly-cadence (S2) axis, not a 2nd archetype axis. See `Core-API/predictive/output/archetype_verdict.md`.
- **This package:** the thin slice + LOCO, then (only if the gate passes) the full engine. The eventual
  production hook is `forecast_panel` step 3 ("Creating predictive analysis") writing `forecast.acys_forecast`.

## Architecture (frozen, top-down, anchor on fleet)
`flights_month ≈ Σ_subfleet [ N_sf(t) × availability_sf × active_intensity_sf × days × seasonality_sf(month) ]`
then network shares (monthly, frozen pairs + seasonal mask) then deterministic conversion to the four
metrics: **flights, cycles, block-hours, km** (+ pax = seats × LF, LF exogenous). Sub-fleet =
(type × seat config × range class) from Cirium `[Aircraft Sub Series]`. Forecast forward = replicate a
typical year off the post-ramp plateau (last ~12 mo), held flat — NOT extrapolation.

## Status
- **§5.1 entry blockers — checked (2026-07-08):**
  - **Operator attribution is CLEAN.** Cirium periods 01-2022…12-2025; of 168,854 tails since 2023-07,
    **12.7% change Operator and 13.9% change Status across months** ⇒ real monthly snapshots (a backfill
    would show 0% variation). Limitation #1 (approximated attribution) is genuinely CLOSED. ⇒ rebuild the
    panel with the date-respecting per-month operator join (like `acys_actuals`), NOT the old af_base
    delivery-date clip.
  - **Primary Usage:** across 4,388 flown tails — Passenger 86.8%, NULL 8.2% (358), Cargo 3.9%; no
    "Multiple". ⇒ needs a seat-density → usage fallback for the 8.2% NULL before conversion branches.
  - Delivery date missing on 8.2% of flown tails — now moot for attribution (per-month operator handles
    it); only affects Age (a pooling strata) ⇒ fallback.

## Next (the gate — do NOT build frozen layers first, §5.3)
1. Regenerate the clean panel (per-month attribution) — the archetype core (discreteness) should survive.
2. Thin vertical slice for ONE schedule-bucket carrier (coarse N × availability × active_intensity ×
   days × seasonality; coarse network shares; deterministic conversion). NO on-demand fork.
3. LOCO: hide the carrier from the panel, forecast the held-out window, read the **reliability curve**
   (80% band covers fact ~80%?). Diagonal ⇒ premise holds; systematically narrow ⇒ revisit the assumption.
