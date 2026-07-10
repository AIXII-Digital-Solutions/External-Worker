# ACYS Forecast — model, coefficients, and field derivation

This documents how the forecast is computed: what each field is, where it comes from, and every
coefficient in the calculation. Two data sources feed the dataset — an aircraft/valuation reference
(per (aircraft, month): operator, type, market value, seats, lease, delivery) and a flight-history source
(real flights per tail).

The output has two `Data Type`s:
- **Actuals** — one row per real flight, history → **yesterday**.
- **Forecast** — projected flights, **today → request date + `FORECAST_HORIZON_YEARS`**.

---

## 1. Coefficients (every tunable)

| Constant | Value | Meaning / why |
|----------|-------|---------------|
| `HISTORY_START` | 2022-07-01 | Earliest month considered (CY2022 floor). |
| `FORECAST_HORIZON_YEARS` | 2 | Forecast reaches CY(`as_of.year + 2 − 1`); for a 2026 request → **CY2027** (last full CY). |
| `LEVEL_L` | 3 | The recent **level** = median of the last 3 deseasonalized months (≤ frontier). Short window = tracks current activity. |
| `SEAS_K` | 6.0 | Seasonal-factor **shrinkage** toward 1.0 by month support: `seasonal[c] = (Σratio + K)/(n + K)`. Thin months are pulled toward "no seasonality". |
| `FRONTIER_FRAC` | 0.6 | A recent month is "complete" if its flights ≥ 0.6 × the trailing-window median (else it's still filling / lagged and is excluded from the fit). |
| `FRONTIER_WINDOW` | 9 | Trailing months the completeness median is measured over. |
| `FORECAST_PAX_LOAD_FACTOR` | 0.8 | `Total PAX = Total Seats × 0.8` (assumed load factor). |

The forecast has **no growth cap and no per-flight retirement rule** — fleet size comes straight from the
reference source (§3), so growth and retirement are whatever the reference says.

---

## 2. Contract Year (day-precise)

A **12-month window ending ON the anchor day**, labelled by its start year. For a request date
`10-Jul-2026`:

```
CY2025 = (10-Jul-2025 , 10-Jul-2026]   i.e. 11-Jul-2025 … 10-Jul-2026
CY2026 = (10-Jul-2026 , 10-Jul-2027]
```

Rule for a flight date `d` vs anchor `(am, ad)`: `CY = year(d) − 1` if `(month, day) ≤ (am, ad)`, else
`year(d)`. So the anchor day is the **last** day of its CY (`10-Jul-2026 → CY2025`; `11-Jul → CY2026`).
Both actuals and forecast follow this per flight, so the anchor month divides between two CYs (Jul→Jul).

---

## 3. Forecast model — the fleet flies continuously

**Principle:** every aircraft in the operator's **latest reference-revision fleet** flies **every**
forecast month; nobody retires unless the reference drops them; future-delivery aircraft join the month
their delivery date lands. Volume per aircraft comes from the type's typical activity; routes come from
the type's typical network for **this** operator (never a route it never flew).

### 3.1 Fit (per sub-fleet = `Aircraft Sub Series`), on months ≤ frontier
- **`seasonal[1..12]`** — month-of-year factor. For each calendar month `c`:
  `ratio_i = flights_i / mean(flights)`, then `seasonal[c] = (Σ ratio + SEAS_K) / (n_c + SEAS_K)` (shrunk toward 1.0).
- **`level`** — median of the last `LEVEL_L` (=3) **deseasonalized** monthly flight totals (`flights / seasonal[cal]`).
- **`base_fleet`** — median flown-tail count of the last `LEVEL_L` months. This is the historical "aircraft that produced `level`".

### 3.2 Frontier
`frontier` = the newest month whose flights ≥ `FRONTIER_FRAC × median(last FRONTIER_WINDOW months)`. Only
months ≤ frontier feed the fit (recent lagging/incomplete months are ignored for fitting but **kept as actuals**).

### 3.3 Per forecast month `m`, per sub-fleet
- **`k` (flights per aircraft this month)** = `round(level / base_fleet × seasonal[cal(m)] × proration)`.
  - `level / base_fleet` = deseasonalized flights **per aircraft**.
  - `proration` = covered-days / days-in-month = 1.0 for full months; the **current** month covers today → month-end (actuals cover the earlier days), and the **final** month covers month-start → the `as_of + HORIZON` day. Both scale `k` AND the day-spread identically.
- **`active fleet`** = latest-revision aircraft of the sub-fleet with `Delivery Date ≤ end of m` (or null). **No retirement.**
- **`route pool`** = the flights of the sub-fleet's **template month** (latest same-calendar-month ≤ frontier; else the sub-fleet's latest month). This is the operator's usual network for that type.
- **Generation:** each active aircraft flies `k` flights, cycling the route pool: `route = pool[((g−1) mod pool_size) + 1]` for `g = 1..k`. Total sub-fleet flights = `k × |active fleet|` → grows automatically with deliveries.

### 3.4 Per-field derivation of a forecast flight
| Field | Source |
|-------|--------|
| Registration, Manufacturer, Aircraft Sub Series, Primary Usage, **Total Seats**, Delivery Date, Lease Type, Lease Dry/Wet, Operational Lessor | the **assigned aircraft** (latest reference revision) |
| Master Series, Operator | the sub-fleet / request operator |
| IATA/ICAO Origin & Destination, Circle Distance, Actual Distance FR, Flight Time, Flight Time FR | the **route** from the pool (the type's real historical route) |
| Date | **spread across the month's days** (current month: from today; final month: up to the `as_of + HORIZON` day) so the day-precise Contract Year divides the anchor month exactly like the actuals (Jul→Jul window, not Aug→Jul) |
| Contract Year | §2 applied to Date |
| **Total PAX** | `Total Seats × FORECAST_PAX_LOAD_FACTOR (0.8)` |
| Time Departed / Time Landed | null (monthly forecast has no clock time) |
| **Agreed Value** | see §4 |

---

## 4. Agreed Value

Base per (aircraft, month) = the reference **Indicative Market Value (US$m)**. Rules:

1. **Wet lease → 0** (`Lease Dry/Wet = 'Wet'`).
2. **Actuals, missing month** → carry the last known value **forward** (fills reference gaps); leading gaps take the earliest known value.
3. **Forecast** → project the aircraft's **own depreciation** forward: `value(m) = last_actual_value + slope × months_after_last`, `slope = LEAST(0, regr_slope(value, month))` (**clamped ≤ 0 — never rises**), floored at 0. A brand-new aircraft with no history holds its reference value flat.

### Weighted Average (`acys_summary_grouped`), per (aircraft, Contract Year)
| Column | Formula |
|--------|---------|
| Agreed Value on Inception | value of the CY's **first** month |
| Agreed Value at End of Contract | value of the CY's **last** month |
| **Weighted Average Agreed Value** | **`(inception + end) / 2`** — the time-average of a straight line start→end, **always between them** (robust to transient mid-year market spikes). |
| Activity-Weighted Average | `Σ(value × flights) / Σ(flights)` over the CY (flight-weighted). |

Wet months and non-positive values are excluded from all four.

---

## 5. Actuals field derivation (for completeness)
One row per real flight. Aircraft attributes from the reference (authoritative operator per (tail, month)
= newest revision; wet-lease/ACMI duplicates collapsed). Route/geo from the flight source. `Circle Distance`
= great-circle (haversine) km between origin & destination; `Age = (Date − Delivery Date)/365.25`.
Flights with no origin **or** no destination airport code are dropped. Lower bound is by **date**:
`first_seen::date > (anchor day in 2022)` — a same-day flight at any clock time on the anchor day still
falls in CY2021 and is dropped, so **CY2021 never appears**.

---

## 6. `forecast.acys_forecast_coefficients` — coefficients for charts

One row per **(Operator, Master Series, Forecast Month)**, written by the forecast model (per-operator
refresh: it deletes its operator's rows, then re-inserts). It exposes every coefficient behind the forecast.

| Column | Meaning |
|--------|---------|
| Operator | operator |
| Master Series | broader type (for chart grouping) |
| Aircraft Sub Series | the **sub-fleet key** the forecast groups by (fit / fleet / routes) |
| Forecast Month / Calendar Month | 1st of the forecast month / its month-of-year (1..12, for the seasonal curve) |
| Frontier | last complete actual month (fit boundary) |
| Level | deseasonalized recent flight level (sub-fleet) |
| Base Fleet | typical flown-tail count |
| Per Aircraft Rate | `Level / Base Fleet` = deseasonalized flights per aircraft |
| Seasonal Factor | `seasonal[Calendar Month]` |
| Proration | covered-days fraction (1.0 except current & final months) |
| Active Fleet | aircraft flying this month (delivered ≤ month) |
| Flights Per Aircraft | `k = round(Per Aircraft Rate × Seasonal Factor × Proration)` |
| Forecast Flights | `k × Active Fleet` — the month's forecast volume |
| Template Month | route-template month used |

Chart recipes:
- **Forecast volume over time:** `SELECT "Forecast Month", sum("Forecast Flights") … GROUP BY 1`.
- **Fleet growth (deliveries arriving):** `sum("Active Fleet")` by `"Forecast Month"`.
- **Seasonal curve:** `"Seasonal Factor"` by `"Calendar Month"` (distinct per `"Master Series"`).
- **Per-aircraft rate / level / base fleet:** one value per sub-fleet.
- `Forecast Flights = Flights Per Aircraft × Active Fleet` and `Flights Per Aircraft = round(Per Aircraft Rate × Seasonal Factor × Proration)` — the whole calculation is reconstructable from the row.
