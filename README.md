# GridWise: a campus energy scheduler with an LLM in front

BUP CSE Fest 2026 hackathon, online preliminary round.

## What it does

Campus operators send short notes such as "do not charge the battery between 2 PM and 4 PM". I use a Mistral model to turn each note into one structured directive, check that directive with plain code, and only then give the numbers to a linear program that finds the cheapest 24-hour schedule.

The model never does any maths. Language goes in, validated JSON comes out, and the solver takes it from there.

## How a request flows

```
POST /optimize-energy
   |
   v
schema check (FastAPI + Pydantic)        bad input gets a 400 here, before any LLM call
   |
   v
interpreter                              cache -> primary model -> retry with feedback
   |                                     -> fallback model -> emergency rule parser
   v
guardrails                               types, hours, ranges, applies semantics
   |
   v
OR-Tools GLOP linear program             cheapest grid cost, all rules as constraints
   |
   v
replay validator                         re-checks every hour and every directive
   |
   v
JSON response
```

The LLM sits inside the interpreter and produces the directives the optimizer uses. Nothing else in the pipeline reads the notes.

Two details are worth knowing. The model returns flat fields (hours, factor, reserve value plus unit, grid cap) and the code builds the official `structured_adjustment` from them, so stray fields never reach the response. Percentage reserves ("50% of capacity") are converted to kWh in code, because I did not want the model doing arithmetic.

When a directive looks wrong, I send the model a list of the problems and ask again instead of quietly dropping it. Two things trigger that: a guardrail failure, or a grounding check that compares the numbers and the single clear time window in the note with what the model returned. A second attempt costs a few seconds, but a dropped constraint costs the whole case.

## Quickstart

You need Python 3.10 or newer and Git. I developed on Python 3.14 on Windows; the Docker image uses 3.11.

```
git clone https://github.com/mhusama/CSE_Hackathon.git
cd CSE_Hackathon
python -m venv venv
```

Activate the environment. On Windows PowerShell:

```
venv\Scripts\Activate.ps1
```

On Linux or macOS:

```
source venv/bin/activate
```

Then install, create your `.env` and put your key in it:

```
pip install -r requirements.txt
cp .env.example .env
```

(PowerShell: `Copy-Item .env.example .env`.) Open `.env` and set `MISTRAL_API_KEY`. A free key from console.mistral.ai works. The file is git-ignored, so the key stays on your machine.

Start the server:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Swagger UI is at `http://localhost:8000/docs`.

## Configuration

Everything comes from environment variables or the `.env` file. Only the key is required.

| Variable | What it does | Default |
| --- | --- | --- |
| `MISTRAL_API_KEY` | Mistral API key. Required. | empty |
| `LLM_MODEL` | Primary model | `open-mistral-nemo` |
| `LLM_FALLBACK_MODEL` | Tried when the primary keeps failing | `mistral-small-latest` |
| `LLM_TIMEOUT_SECONDS` | Hard limit for one Mistral call | `10` |
| `LLM_MAX_RETRIES` | Attempts per model. Later attempts carry feedback | `2` |
| `LLM_TOTAL_BUDGET_SECONDS` | Limit for the whole interpretation step (the judge allows 30 s per request) | `22` |
| `MISTRAL_MIN_INTERVAL_SECONDS` | Minimum gap between Mistral calls, shared by all models. `0` turns it off | `1.1` |
| `LLM_CACHE_SIZE` | Interpretations kept in memory | `512` |
| `MISTRAL_BASE_URL` | API base URL | `https://api.mistral.ai/v1` |
| `PORT` | HTTP port | `8000` |
| `LOG_LEVEL` | `debug`, `info`, `warning` or `error` | `info` |

I ended up with `open-mistral-nemo` as primary for a boring reason. On my free key `mistral-small-latest` returned 429 on every call, even with calls spaced 1.1 seconds apart, and nemo did not. Your account may behave differently, so run the eval below and swap the two names if it does.

## Calling the API

Health check:

```
curl http://localhost:8000/health
```

It returns `{"status":"ok"}`.

`docs/sample_request.json` is public sample SAMPLE-06 (three notes, one of them a distractor). With curl:

```
curl -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" -d @docs/sample_request.json
```

PowerShell aliases `curl` to something else, so use this instead:

```
Invoke-RestMethod -Uri http://localhost:8000/optimize-energy -Method Post -ContentType "application/json" -Body (Get-Content docs/sample_request.json -Raw) | ConvertTo-Json -Depth 6
```

Trimmed response (the explanation wording comes from the model and changes between runs):

```json
{
  "scenario_id": "SAMPLE-06",
  "directive_interpretation": [
    {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
     "structured_adjustment": {"hours": [10, 11], "factor": 0.5}, "explanation": "..."},
    {"note_index": 1, "applies": true, "directive_type": "no_charge_window",
     "structured_adjustment": {"hours": [14, 15]}, "explanation": "..."},
    {"note_index": 2, "applies": false, "directive_type": "no_op",
     "structured_adjustment": null, "explanation": "..."}
  ],
  "hourly_plan": [ "... 24 entries ..." ],
  "total_grid_kwh": 2395.0,
  "total_cost_bdt": 34090.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Applied 2 directive(s): solar_reduction, no_charge_window. Ignored 1 irrelevant note(s). ..."
}
```

Status codes: 200 on success, 400 for malformed JSON or a structurally invalid request, 422 when the scenario is infeasible even with no directives, 500 for anything unexpected (no stack traces in the body).

### Directive types

| Type | Meaning | `structured_adjustment` |
| --- | --- | --- |
| `solar_reduction` | Usable solar drops during the hours. `factor` is the fraction that remains, so an 80% reduction is 0.2 | `{"hours": [...], "factor": n}` |
| `minimum_battery_reserve` | Battery energy stays at or above a level | `{"hours": [...], "minimum_energy_kwh": n}` |
| `no_charge_window` | No charging in the hours | `{"hours": [...]}` |
| `no_discharge_window` | No discharging in the hours | `{"hours": [...]}` |
| `max_grid_window` | Grid import per hour is capped | `{"hours": [...], "max_grid_kwh": n}` |
| `no_op` | The note does not affect today's schedule | `null` |

Windows start at the first hour and stop before the last, so 1 PM to 3 PM is `[13, 14]`.

## Guardrails

Whatever the model returns, these rules run before the optimizer sees it:

- Only the six directive types above are accepted, and `applies` is `false` for `no_op` and `true` for everything else.
- Hours must be unique integers from 0 to 23 in ascending order. If the model returns 24 (it does that for "until midnight"), I drop the 24 rather than lose the directive.
- `factor` must lie between 0 and 1. A reserve must be finite, not negative, and no larger than the battery capacity. A grid cap must be finite and not negative.
- The response has exactly one entry per note, in `note_index` order. A short answer from the model is padded with `no_op` and triggers a retry.

If a directive still fails after the retry, that note becomes `no_op`. It does not crash the request and it never invents a new directive type.

## The optimizer

OR-Tools GLOP solves one linear program per request. Grid, solar used, charge, discharge and battery energy are variables for each hour; energy balance, battery limits, rate limits, end-of-day neutrality and every directive are constraints; the objective is total grid cost. It finishes in milliseconds.

The LP can, in a tie, charge and discharge in the same hour. I net those two before building the plan so each hour has one valid battery action.

If the interpreted directives cannot all be met together, the service drops the smallest set of them that makes the problem feasible (the cheapest such choice if several work) and says so in `plan_summary`. `directive_interpretation` still shows what the note was read as. I added this because a 422 earns nothing, while a valid plan for the other constraints earns something.

After solving, a replay validator walks the plan hour by hour and re-checks balance, battery state, limits, solar use and every directive. The totals in the response come from that replay, not from the solver.

## Docker

The GitHub Actions workflow in `.github/workflows/ci.yml` runs the tests and then pushes an image to GHCR on every push to `master`. To run it:

```
docker pull ghcr.io/mhusama/gridwise-energy-api:latest
docker run -d -p 8000:8000 -e MISTRAL_API_KEY="your_key_here" --name gridwise ghcr.io/mhusama/gridwise-energy-api:latest
curl http://localhost:8000/health
```

The repo and this package are kept private during the event and made public after the submission deadline, as the rules ask. To pin a version, use the commit SHA tag from the workflow run instead of `latest`.

Building it yourself:

```
docker build -t gridwise-energy-api:latest .
docker run -d -p 8000:8000 -e MISTRAL_API_KEY="your_key_here" --name gridwise gridwise-energy-api:latest
```

Or `docker-compose up -d --build` with the key in `.env`.

The container binds to `0.0.0.0`, follows `$PORT` and has a health check on `/health`. `.dockerignore` keeps `.env` out of the image, and the key only arrives at run time.

## Public deployment

**Public base URL:** https://cse-hackathon-xd9l.onrender.com

The judge has to reach `GET /health` and `POST /optimize-energy` for the whole evaluation window, so a laptop is not enough. The live service runs on Render as a Docker web service built from this repo, with `MISTRAL_API_KEY` set in the Render dashboard and the health check path set to `/health`. `render.yaml` describes the same setup if you want to redeploy it. Any other host with one warm instance would work too, since free instances that sleep can take more than a minute to wake up.

Test from a different network before you submit:

```
curl https://cse-hackathon-xd9l.onrender.com/health
```

## Tests

```
python -m pytest -q
```

The suite has 52 tests. They cover the API status codes, every guardrail, retry with feedback, timeout enforcement, model failover, the cache, the 429 wait, the request throttle, the emergency parser, infeasible-directive relaxation, the optimizer, the replay validator and the 10 public samples. The Mistral tests use a mocked HTTP transport, so they need no key and no network.

Read the next part before trusting the sample-case tests, because they only check the optimizer. They feed the reference directives straight to the solver.

To test the real model, add your key and run:

```
python scripts/run_sample_cases.py --use-llm
python scripts/eval_interpretation.py
```

The first runs the full pipeline on the 10 public samples and compares both the interpretation and the cost. The second scores interpretation alone on relevance, directive type, hours and values (the same four things the judge checks), lists every miss and reports latency. It also prints which source answered each scenario. Add `--rules` to score only the emergency parser with no key needed, or `--hard-only` to run just the harder 37-note set.

`scripts/check_live.py <url>` is a black-box test for a running server, local or hosted. It checks `/health`, bad input, the 10 samples with latency, and a parallel burst.

## What I measured

With `open-mistral-nemo` as primary:

- All 10 public samples matched the reference cost exactly, through the real model. One call timed out at 10 seconds during that run, the retry succeeded, and the case still passed.
- The interpretation eval covered 40 scenarios and 48 notes: relevance 48/48, type 48/48, hours 37/37, values 24/24.

I would not read too much into that 100%. Eighteen of the 48 notes come from the public samples. The other 30 are paraphrases written for this repo, not taken from the organizers, so they are probably easier than the hidden set.

So I wrote a second, harder set of 37 notes (`tests/data/hard_paraphrases.json`) with unusual wording, 24-hour times, "midnight", windows that cross midnight, percentages of capacity, and distractors that mention a time or an energy word without changing anything. One of them is a prompt-injection attempt. Run separately with the same model: relevance 37/37, type 37/37, hours 30/30, values 19/19. Interpretation took a mean of 1.54 s per scenario (p95 2.87 s, max 3.96 s) when measured from my machine.

Both sets were written by me and my assistants, so they can still miss wording the organizers use. Add more notes to either JSON file and rerun the eval.

## Known limitations

- **Free-tier rate limits.** Calls are spaced 1.1 seconds apart. Sequential requests are fine, but a burst of concurrent requests queues up behind that gap and the last ones can run into the 30-second limit. The cache helps when notes repeat.
- **`mistral-small-latest` on a free key.** It returned 429 on every call for me. That is why it is only the fallback.
- **Emergency parser.** It is used only when every LLM call fails, and the response `plan_summary` says so. It handles the common phrasings. I wrote it against the same sample and paraphrase sets I test with, so its 100% there is a smoke test and nothing more.
- **Bare hours.** "From one until three" is read as the afternoon. A note that is truly ambiguous can be misread.
- **Model names.** The names come from Mistral's public docs, not from a live catalogue. If the API says a model is not found, change `LLM_MODEL`.
- **Cache.** It lives in memory, one per running instance, and is gone after a restart.
- **Overlapping solar cuts.** Two solar reductions on the same hour multiply. The spec does not say what should happen, so this is my call.

## Secrets

No key is in the code, the image or the git history. The `.env` file is git-ignored and excluded from Docker builds. Error messages and logs never include the key.

## Credits

| Tool | Used for |
| --- | --- |
| FastAPI, Uvicorn | HTTP service |
| Pydantic, pydantic-settings | request validation and configuration |
| Google OR-Tools (GLOP) | linear programming solver |
| httpx | calls to the Mistral API |
| Mistral AI API | interpreting operator notes |
| pytest, pytest-asyncio | tests |
| Claude, Antigravity (Gemini models), ChatGPT and puku.sh | AI coding assistants used while building, debugging and writing docs |
