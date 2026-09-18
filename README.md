# GridWise — Smart Campus Energy Optimization Service
## LLM-Assisted Operator Directive Interpretation & Energy Scheduling
**BUP CSE Fest 2026 Hackathon · Online Preliminary Round**

---

## 1. Overview & System Architecture

GridWise is an automated, production-grade energy scheduling service developed for the BUP CSE Fest 2026 Hackathon. It bridges natural-language campus operator directives with mathematical linear programming to minimize 24-hour campus grid electricity costs while maintaining 100% physical and operational constraint validity.

```
                  POST /optimize-energy
                            │
                            ▼
              ┌───────────────────────────┐
              │  FastAPI Schema & Checks  │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │  LLM Directive Extraction │ (Google Gemini 2.0 Flash)
              │  Single batch JSON call   │
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │  Deterministic Guardrails │ (Validation, clamping, safe fallback)
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │    OR-Tools LP Solver     │ (Exact linear cost minimization)
              └─────────────┬─────────────┘
                            │
                            ▼
              ┌───────────────────────────┐
              │ Deterministic Post-Replay │ (100% hour-by-hour constraint verification)
              └─────────────┬─────────────┘
                            │
                            ▼
                 JSON Response (200 OK)
```

### Key Architectural Pillars:
1. **LLM Directive Interpretation**: Uses Google Gemini (`gemini-2.0-flash` by default) via the official `google-genai` SDK with native JSON structured output. All 1–3 operator notes are interpreted in a single batch request to minimize latency and token usage.
2. **Deterministic Guardrails**: Untrusted LLM outputs are rigorously validated:
   - Directive types restricted strictly to the 6 allowed enums.
   - Applies semantics enforced (`no_op` ↔ `false`, all others ↔ `true`).
   - Hours validated to unique integers `0..23` in ascending order with start-inclusive/end-exclusive windowing.
   - Factors validated within `[0.0, 1.0]`.
   - Battery reserve validated within `[0, capacity]`.
   - Grid import caps validated as finite and non-negative.
   - Any unfixable malformed LLM directive falls back deterministically to safe `no_op`.
3. **Exact Mathematical Optimization**: Uses Google OR-Tools GLOP Linear Programming solver. Solves in milliseconds with global mathematical optimality for cost minimization.
4. **Post-Solve Replay Validator**: Replays the entire 24-hour schedule hour-by-hour verifying energy balance, battery state transitions, battery bounds, charge/discharge rates, solar usage limits, directive constraints, and end-of-day battery neutrality.

---

## 2. Supported Directive Types

| Directive Type | Meaning | Required `structured_adjustment` |
|---|---|---|
| `solar_reduction` | Reduces usable solar during specific hours | `{"hours": [int, ...], "factor": float}` *(factor = remaining usable fraction)* |
| `minimum_battery_reserve` | Enforces minimum battery energy reserve | `{"hours": [int, ...], "minimum_energy_kwh": float}` |
| `no_charge_window` | Disallows battery charging during hours | `{"hours": [int, ...]}` |
| `no_discharge_window` | Disallows battery discharging during hours | `{"hours": [int, ...]}` |
| `max_grid_window` | Caps grid electricity import in hours | `{"hours": [int, ...], "max_grid_kwh": float}` |
| `no_op` | Irrelevant or distractor note | `null` *(with `applies: false`)* |

---

## 3. Environment Variables & Configuration

Configuration is managed via environment variables or a `.env` file in the root directory:

| Variable | Description | Default | Required |
|---|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API key (free from [Google AI Studio](https://aistudio.google.com)) | `""` | Yes (for live LLM) |
| `LLM_MODEL` | Gemini model name | `gemini-2.0-flash` | No |
| `PORT` | HTTP server port | `8000` | No |
| `LOG_LEVEL` | Logging verbosity (`debug`, `info`, `warning`, `error`) | `info` | No |
| `SOLVER_TIMEOUT_SECONDS` | Solver timeout limit | `25.0` | No |
| `LLM_TIMEOUT_SECONDS` | Gemini API call timeout | `20.0` | No |
| `LLM_MAX_RETRIES` | Retries with backoff for Gemini API | `2` | No |

Template is available in `.env.example`.

---

## 4. Local Quickstart (Clean Environment)

### Prerequisites
- Python 3.10+ (tested on Python 3.11, 3.12, and 3.14)
- Git

### Step-by-step Setup:

```bash
# 1. Clone repository
git clone <REPO_URL>
cd CSE_Hackathon

# 2. Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and insert your free Gemini API key:
# GEMINI_API_KEY=your_actual_key_here

# 5. Run the service
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The service will be live at `http://localhost:8000`.

---

## 5. Docker Deployment

### Building and Running with Docker:

```bash
# Build the Docker image
docker build -t gridwise-energy-api:latest .

# Run the container
docker run -d \
  -p 8000:8000 \
  -e GEMINI_API_KEY="your_api_key_here" \
  --name gridwise-service \
  gridwise-energy-api:latest
```

### Or using Docker Compose:

```bash
# Set GEMINI_API_KEY in .env, then:
docker-compose up -d --build
```

### Checking Container Health:
```bash
docker ps
curl http://localhost:8000/health
```

---

## 6. API Endpoints & Usage Examples

### 1. Health Endpoint (`GET /health`)
Readiness check for the judging harness.

**Request:**
```bash
curl -X GET http://localhost:8000/health
```

**Response (200 OK):**
```json
{
  "status": "ok"
}
```

---

### 2. Main Optimization Endpoint (`POST /optimize-energy`)
Interprets operator notes, validates directives, runs the optimizer, verifies all constraints, and returns the 24-hour plan.

**Request:**
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "DEMO-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next months registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 5},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 5, "tariff_bdt_per_kwh": 8},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 20, "tariff_bdt_per_kwh": 10},
      {"hour": 8, "demand_kwh": 150, "solar_kwh": 50, "tariff_bdt_per_kwh": 12},
      {"hour": 9, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 180, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 180, "solar_kwh": 170, "tariff_bdt_per_kwh": 14},
      {"hour": 14, "demand_kwh": 170, "solar_kwh": 140, "tariff_bdt_per_kwh": 13},
      {"hour": 15, "demand_kwh": 165, "solar_kwh": 90, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 45, "tariff_bdt_per_kwh": 18},
      {"hour": 17, "demand_kwh": 185, "solar_kwh": 10, "tariff_bdt_per_kwh": 22},
      {"hour": 18, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 28},
      {"hour": 19, "demand_kwh": 215, "solar_kwh": 0, "tariff_bdt_per_kwh": 30},
      {"hour": 20, "demand_kwh": 205, "solar_kwh": 0, "tariff_bdt_per_kwh": 26},
      {"hour": 21, "demand_kwh": 175, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 135, "solar_kwh": 0, "tariff_bdt_per_kwh": 10},
      {"hour": 23, "demand_kwh": 105, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
    ],
    "battery": {
      "capacity_kwh": 300,
      "initial_energy_kwh": 110,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

**Response (200 OK):**
```json
{
  "scenario_id": "DEMO-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {
        "hours": [12, 13],
        "factor": 0.25
      },
      "explanation": "Solar availability is reduced to 25% during panel cleaning from noon to 2 PM."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Administrative note with no effect on energy scheduling."
    }
  ],
  "hourly_plan": [ ... 24 hourly entries ... ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Applied 1 directive(s): solar_reduction. Ignored 1 irrelevant note(s). Optimized schedule achieves 38365.00 BDT total cost using 2692.50 kWh from grid."
}
```

---

## 7. Testing & Verification

### Automated Test Suite:
Run the complete unit and integration test suite:
```bash
pytest -v
```
All 22 automated tests verify:
- API endpoints (`GET /health`, `POST /optimize-energy`, input validation errors)
- Guardrails for all 6 directive types, clamping, index correction, and safe fallbacks
- LP optimizer constraints (baseline, solar reduction, charge/discharge windows, reserve, grid caps)
- Post-solve replay validator (balance, rates, bounds, neutrality, solar limits)
- **All 10 public sample cases** (achieving exact optimal reference cost)

### Running Sample Cases Script:
```bash
# Test all 10 public sample cases:
python scripts/run_sample_cases.py

# Or with live Gemini LLM interpretation (requires GEMINI_API_KEY):
python scripts/run_sample_cases.py --use-llm
```

Sample Verification Output:
```
=======================================================
  GridWise Sample Case Verification (Total: 10 cases)
=======================================================
Case ID      | Directives     | Calc Cost   | Ref Cost    | Diff (BDT)  | Status
---------------------------------------------------------------------------
SAMPLE-01    | 2              | 38365.00    | 38365.00    | +0.00       | PASS
SAMPLE-02    | 1              | 42885.00    | 42885.00    | +0.00       | PASS
SAMPLE-03    | 1              | 35480.00    | 35480.00    | +0.00       | PASS
SAMPLE-04    | 1              | 40495.00    | 40495.00    | +0.00       | PASS
SAMPLE-05    | 1              | 33950.00    | 33950.00    | +0.00       | PASS
SAMPLE-06    | 3              | 34090.00    | 34090.00    | +0.00       | PASS
SAMPLE-07    | 2              | 38550.00    | 38550.00    | +0.00       | PASS
SAMPLE-08    | 2              | 37665.00    | 37665.00    | +0.00       | PASS
SAMPLE-09    | 2              | 34873.00    | 34873.00    | +0.00       | PASS
SAMPLE-10    | 3              | 41620.00    | 41620.00    | +0.00       | PASS
---------------------------------------------------------------------------
Summary: 10/10 PASSED, 0 FAILED.
```

---

## 8. External Dependencies & Credits

- **FastAPI** (`0.115+`) & **Starlette**: High-performance asynchronous REST API framework
- **Pydantic** (`v2.9+`) & **pydantic-settings**: Strict data validation and schema enforcement
- **Google OR-Tools** (`v9.11+` / `v9.15+`): Industrial-grade mathematical optimization (GLOP LP solver)
- **Google GenAI SDK** (`google-genai` `v1.14+` / `v2.24+`): Upstream SDK for Google Gemini models
- **Uvicorn** (`0.30+`): ASGI web server implementation
- **Pytest** & **pytest-asyncio**: Test runner and async testing utilities

---

## 9. Limitations & Edge Cases Handled

1. **Simultaneous Charging and Discharging**: Linear programming could theoretically set non-zero charge and discharge in degenerate equal-cost scenarios. GridWise automatically nets them prior to building the plan, guaranteeing single-action physical validity.
2. **LLM Formatting Variance**: If the LLM wraps the response in a container dictionary (e.g. `{"directives": [...]}`), the parser extracts the array automatically. If an individual directive is malformed, guardrails safely clamp values or default to `no_op` rather than crashing the request.
3. **No-secret guarantee**: No keys or credentials are baked into images, code, or repositories.
