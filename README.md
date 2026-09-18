# GridWise — Smart Campus Energy Optimization Service
## LLM-Assisted Operator Directive Interpretation & Energy Scheduling
**BUP CSE Fest 2026 Hackathon · Online Preliminary Round**

---

## 1. Overview & System Architecture

GridWise turns natural-language campus operator notes into structured directives with an LLM, checks them with deterministic guardrails, and then solves the 24-hour schedule as a linear program (exact minimum grid cost).

```
POST /optimize-energy
   |
   v
FastAPI schema checks (400 on malformed input, always before any LLM work)
   |
   v
LLM interpretation (Mistral chat API, JSON mode, temperature 0)
   |  retry with feedback if guardrail/grounding checks find a problem
   |  fail over to a second model if the first keeps failing
   |  in-memory cache for identical notes + battery capacity
   |  emergency rule parser ONLY if every LLM call failed (never a 500)
   v
Deterministic guardrails (types, hours, ranges, applies semantics, hour-24 repair)
   v
OR-Tools GLOP LP (exact optimum). If directives make it infeasible, the
   least-costly conflicting directive is relaxed and reported in plan_summary
   v
Replay validator (hour-by-hour re-check of every rule and directive)
   v
JSON response
```

### Key pillars

1. **LLM directive interpretation.** Every note is read by a Mistral model (`LLM_MODEL`, default `open-mistral-nemo`) through the Mistral REST API (`/v1/chat/completions`, JSON mode, called with `httpx`). The model returns flat JSON entries (hours, factor, reserve value + unit, grid cap). The code builds the official `structured_adjustment` from it, so extra fields never leak, and percent-of-capacity reserves are converted to kWh in code, not by the model. All notes go in one call to keep latency low.
2. **Self-correction.** The guardrails plus a grounding check (numbers in the note vs extracted values, one clear time window vs returned hours) can send the model a list of problems and ask again, instead of silently dropping the directive.
3. **Reliability.** Per-call timeout (`LLM_TIMEOUT_SECONDS`) and a total budget (`LLM_TOTAL_BUDGET_SECONDS`) are enforced. After the primary model fails, `LLM_FALLBACK_MODEL` is tried. If every LLM call fails, a small rule-based parser keeps the service answering; the response `plan_summary` says so. This parser is a fallback only, the LLM is always tried first.
4. **Exact optimization.** OR-Tools GLOP linear programming, solved in milliseconds.
5. **Post-solve replay.** Energy balance, battery transitions and bounds, rate limits, solar limits, all directives and end-of-day neutrality are re-checked before responding.

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

Set them in the environment or in a `.env` file (template: `.env.example`). Never commit real keys.

| Variable | Description | Default | Required |
| --- | --- | --- | --- |
| `MISTRAL_API_KEY` | Mistral API key ([console.mistral.ai](https://console.mistral.ai), free Experiment tier works) | `""` | Yes |
| `LLM_MODEL` | Primary Mistral model | `open-mistral-nemo` | No |
| `LLM_FALLBACK_MODEL` | Used if the primary keeps failing | `mistral-small-latest` | No |
| `LLM_TIMEOUT_SECONDS` | Hard limit for one Mistral call | `10` | No |
| `MISTRAL_MIN_INTERVAL_SECONDS` | Minimum gap between Mistral calls (free-tier rate limit); `0` = off | `1.1` | No |
| `LLM_MAX_RETRIES` | Attempts per model (later attempts carry feedback) | `2` | No |
| `LLM_TOTAL_BUDGET_SECONDS` | Whole interpretation step limit (judge limit is 30 s) | `22` | No |
| `LLM_CACHE_SIZE` | Cached interpretations | `512` | No |
| `PORT` | HTTP port | `8000` | No |
| `LOG_LEVEL` | `debug`, `info`, `warning`, `error` | `info` | No |

The `-latest` aliases follow Mistral's current model of that size. For higher accuracy at more latency, set `LLM_MODEL=mistral-large-latest`. Free-tier rate limits are not published exactly (see the Limits page in the Mistral console), so check them before judging.

---

## 4. Local Quickstart (Clean Environment)

### Prerequisites
- Python 3.10+ (tests run on 3.12; the Docker image uses 3.11)
- Git

### Step-by-Step Setup:

#### 1. Clone Repository
```bash
git clone https://github.com/mhusama/CSE_Hackathon.git
cd CSE_Hackathon
```

#### 2. Create and Activate Virtual Environment

- **Windows (PowerShell):**
  ```powershell
  python -m venv venv
  # If PowerShell script execution is restricted, run:
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
  .\venv\Scripts\Activate.ps1
  ```

- **Windows (Command Prompt / CMD):**
  ```cmd
  python -m venv venv
  venv\Scripts\activate.bat
  ```

- **Linux / macOS:**
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

#### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 4. Configure Environment Variables
Copy the template configuration to create your `.env` file:

- **Windows (PowerShell):**
  ```powershell
  Copy-Item .env.example .env
  ```
- **Windows (CMD):**
  ```cmd
  copy .env.example .env
  ```
- **Linux / macOS:**
  ```bash
  cp .env.example .env
  ```

Open `.env` in any text editor and insert your Mistral API key (free from [console.mistral.ai](https://console.mistral.ai)):
```dotenv
MISTRAL_API_KEY=your_actual_key_here
LLM_MODEL=open-mistral-nemo
PORT=8000
LOG_LEVEL=info
```

#### 5. Run the API Server

- **Using Uvicorn CLI:**
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
  ```

- **Or Using Python Module:**
  ```bash
  python -m app.main
  ```

Once started, the API is accessible at:
- **Service Base URL:** `http://localhost:8000`
- **Interactive Swagger UI:** `http://localhost:8000/docs`
- **ReDoc Alternative UI:** `http://localhost:8000/redoc`
- **Health Readiness Check:** `http://localhost:8000/health`

---

## 5. Docker Deployment

### Pull the prebuilt fallback image

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs the tests, then builds and pushes the image on every push to `master`:

```
docker pull ghcr.io/mhusama/gridwise-energy-api:latest
docker run -d -p 8000:8000 -e MISTRAL_API_KEY="your_api_key_here" --name gridwise ghcr.io/mhusama/gridwise-energy-api:latest
curl http://localhost:8000/health
```

Pin an exact version with the commit tag (`:<git sha>`) shown in the workflow run. The package must be set to **public** in GitHub (Packages > gridwise-energy-api > Package settings) so judges can pull it. The image contains no secrets (`.dockerignore` excludes `.env`); the key is passed at run time.

### Build it yourself

```
docker build -t gridwise-energy-api:latest .
docker run -d -p 8000:8000 -e MISTRAL_API_KEY="your_api_key_here" --name gridwise-service gridwise-energy-api:latest
```

Or with Compose (set `MISTRAL_API_KEY` in `.env` first): `docker-compose up -d --build`

The container honours `$PORT`, binds to `0.0.0.0`, and has a health check on `/health`.

---

## 6. Public Deployment (required for judging)

The judge must reach `GET /health` and `POST /optimize-energy` for the whole evaluation window, so deploy to an always-on host, not a laptop.

**Public base URL:** `<PUBLIC_BASE_URL>` (fill in after deploying)

Any Docker-capable host works. Two easy routes:

- **Render:** New > Blueprint > select this repo (`render.yaml` is included). Add `MISTRAL_API_KEY` in the dashboard. Use an always-on instance: free instances sleep and can take longer than 60 s to wake.
- **Cloud Run / Railway / Fly.io:** deploy the Dockerfile, set `MISTRAL_API_KEY`, keep at least one instance warm.

Check from outside your network before submitting:

```
curl https://<PUBLIC_BASE_URL>/health
curl -X POST https://<PUBLIC_BASE_URL>/optimize-energy -H "Content-Type: application/json" -d @docs/sample_request.json
```

Last resort for a quick demo only: a tunnel (`cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`). Tunnels die when the laptop sleeps or the terminal closes, so do not rely on one for judging.

---

## 7. API Endpoints & Usage Examples

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

#### Option A: Via Interactive Swagger UI (Easiest)
1. Open `http://localhost:8000/docs` in your browser.
2. Click on `POST /optimize-energy` -> click **"Try it out"**.
3. Paste the sample payload below into the Request body and click **"Execute"**.

#### Option B: Windows PowerShell (`Invoke-RestMethod`)
```powershell
$payload = @'
{
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
}
'@

Invoke-RestMethod -Uri "http://localhost:8000/optimize-energy" -Method Post -ContentType "application/json" -Body $payload | ConvertTo-Json -Depth 6
```

#### Option C: Linux / macOS / Git Bash (`curl`)
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

## 8. Testing & Verification

```
pytest -v
```

The 39 automated tests cover: API endpoints and status codes (including 400 before any LLM work and a valid answer with no API key), guardrails for all directive types, hour-24 repair, retry with feedback, per-call timeout enforcement, model failover, cache, the rule-based fallback, infeasible-directive relaxation, the LP optimizer, the replay validator, and the 10 public sample cases.

Note: `test_samples.py` and `python scripts/run_sample_cases.py` feed the **reference** directives to the optimizer, so they verify the optimizer, not the LLM.

### Measure LLM interpretation accuracy (needs a key)

```
python scripts/eval_interpretation.py           # real Mistral path: 10 sample cases + 30 paraphrase notes
python scripts/eval_interpretation.py --rules   # emergency fallback parser only, no key
python scripts/run_sample_cases.py --use-llm    # full pipeline; also compares directives with the reference
```

The eval prints accuracy for relevance, directive type, hours and values (the same axes the judge scores) and lists every miss. Add your own paraphrases to `tests/data/paraphrases.json`.

---

## 9. External Dependencies & Credits

- **FastAPI** (`0.115+`) & **Starlette**: High-performance asynchronous REST API framework
- **Pydantic** (`v2.9+`) & **pydantic-settings**: Strict data validation and schema enforcement
- **Google OR-Tools** (`v9.11+` / `v9.15+`): Industrial-grade mathematical optimization (GLOP LP solver)
- **Mistral AI API** (REST, via **httpx**): LLM for operator-note interpretation
- **Uvicorn** (`0.30+`): ASGI web server implementation
- **Pytest** & **pytest-asyncio**: Test runner and async testing utilities
- **AI coding assistants used during development:** <list the tools you used, as the rulebook requires>

---

## 10. Limitations & Behaviour Worth Knowing

1. **Simultaneous charge/discharge** in degenerate LP ties is netted before the plan is built, so every hour has one valid battery action.
2. **Guardrails repair or retry, then neutralise.** Hour 24 is dropped; wrong types, factors or numbers trigger a retry with feedback. If the model still returns something invalid, that note becomes `no_op` rather than crashing the request.
3. **Emergency fallback parser.** Used only when every LLM call fails. It handles common phrasings, not every paraphrase, so accuracy in that mode is lower than with the LLM. The response `plan_summary` states when it was used.
4. **Infeasible directives are relaxed.** If the interpreted directives cannot all be satisfied together, the cheapest single relaxation that becomes feasible is applied and named in `plan_summary`; `directive_interpretation` still shows what was read. If the scenario is infeasible even with no directives, the API returns 422.
5. **Bare hours without AM/PM** ("from one until three") are read as afternoon by the model prompt; genuinely ambiguous notes may be misread.
6. **Provider limits.** Free Mistral keys have rate limits. Use a key with enough quota for the judging window.
7. **No secrets** are stored in the code, image or repository history.
