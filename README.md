# Crossover

**Name three films you love and get music. Name three artists and get films. Then see, in numbers, how a plain LLM's guesses compare with Qloo's taste graph.**

Live demo: _added once deployed_

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/).

## What it does

You give Crossover three things you love in one domain. It answers in a different domain, twice:

1. **Qloo-grounded.** An agent finds your three inputs in Qloo's catalog, asks the taste graph for the best fits in the other domain, and reads back how much each input contributed to each pick.
2. **Plain LLM.** An LLM gets the same three inputs and the same request, with no Qloo data. The page names the exact model used for every run.

Most projects say that grounding beats guessing. Crossover puts both answers side by side and measures the difference on screen, using Qloo itself as the measuring instrument: every LLM pick is looked up in Qloo's catalog and scored against the same three inputs.

## How the evaluation works

### The key fact it rests on

Qloo's affinity score is absolute. An entity gets the same score whether it shows up in a top-8 list or is scored by request through `filter.results.entities`. That means the LLM's picks can be scored on exactly the same scale as Qloo's own picks. This was confirmed against the live API before anything was built on it (see [docs/qloo-api-notes.md](docs/qloo-api-notes.md)).

### The steps

| Step | Qloo-grounded side | Plain LLM side |
|---|---|---|
| 1 | Look up the three inputs with `GET /search` | Receives the same three inputs, by the names Qloo matched |
| 2 | `GET /v2/insights` with the inputs as `signal.interests.entities` and `filter.type` set to the other domain | One prompt, no tools, no Qloo data |
| 3 | Read `query.affinity` and `query.explainability` for each pick | Each pick is looked up with `GET /search`, then all of them are scored in one `GET /v2/insights` call using `filter.results.entities` |

### The metrics

| Metric | What it is | How it is computed |
|---|---|---|
| **Taste fit** | How well the picks fit the three inputs | Mean of Qloo's `query.affinity` (0 to 1) |
| **How mainstream** | Whether a list leans on the obvious | Median of Qloo's `popularity` percentile, shown as "top X%" |
| **Variety** | How different the picks are from each other | Mean pairwise Jaccard distance between the picks' Qloo genre tags (0 to 1) |
| **Agreement** | How much the two lists share | Count of Qloo entity ids in both lists |
| **Coverage** | How much of the LLM's list could be checked | Count of LLM picks matched and scored |

The code is in [app/metrics.py](app/metrics.py). Every function there is plain arithmetic on fields Qloo returned, and each one is tested against answers worked out by hand in [tests/test_metrics.py](tests/test_metrics.py).

### The rules it follows

- **Nothing is estimated.** If a field is missing, that pick is left out of that metric, and the screen says how many picks the number is based on ("based on 7 of 8 picks").
- **A pick only counts on a confident match.** Qloo's search is fuzzy: searching for an invented name still returns something. So an LLM pick is matched only when the normalised name is identical, and for films the release year must also agree to within one year. Otherwise it is shown as "not measured" with the reason.
- **Unmeasured is not zero.** A pick Qloo cannot score does not drag the LLM's average down. It is simply not in the average.
- **The same fields for both sides.** Qloo reports slightly different popularity for one entity in `/search` and `/v2/insights`, so both lists take their numbers from insights responses only.
- **The baseline gets a fair question.** The prompt asks for the same number of picks and does not steer toward famous or obscure choices. The exact prompt is shown on the page for every run.

### What the numbers do not prove

This matters more than the numbers.

- **Taste fit is Qloo's own score, and Qloo's list is ranked by it.** Qloo will lead on that metric by construction. It is not an independent referee. The useful reading is the size of the gap, and which of the LLM's picks the graph agrees with. The page says this next to the number.
- **None of this measures whether a person would enjoy a pick.** That needs human ratings, which this project does not have.
- **How mainstream and Variety are descriptions, not scores.** Neither direction is better in general.
- **The baseline is an open-weight model on a free tier.** The live demo uses `openai/gpt-oss-120b` served by Groq, because it is free to run. A top commercial model may guess better, so read the gap as "this model versus the graph", not "every LLM versus the graph". Any OpenAI-compatible provider can be swapped in with two settings.
- **Artists with the same name.** There is no release year to check for an artist, so when several share an exact name the most popular one in Qloo is used.

### Why only films and music

Travel, books and restaurants were tested as output domains and left out. Qloo's API accepts those pairings, but the results did not hold up: for example, three European romance films returned Beijing, Tianjin and Wuhan as travel destinations, which looks like where the films' fans live, not where they would want to go. Measuring an LLM against that score would have "proved" Qloo better on results a person would find worse. Films and music, in both directions, returned results that were specific and convincing across every taste profile tried. The full table is in [docs/qloo-api-notes.md](docs/qloo-api-notes.md).

## Run it yourself

You need Python 3.12 or newer and a Qloo hackathon API key.

```bash
git clone https://github.com/anamankev/crossover.git
cd crossover

python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env               # then open .env and paste your keys
uvicorn app.main:app --reload
```

Open http://localhost:8000.

| Setting | Needed? | What it is |
|---|---|---|
| `QLOO_API_KEY` | yes | Your Qloo hackathon key |
| `QLOO_API_URL` | no | Defaults to `https://hackathon.api.qloo.com` |
| `LLM_API_KEY` | for the comparison | A key for the baseline LLM. A free one from [console.groq.com](https://console.groq.com) works with the defaults. Without it the Qloo side still works and the LLM side says it is not connected. It never shows invented results. |
| `LLM_BASE_URL` | no | Any OpenAI-compatible API. Defaults to Groq, `https://api.groq.com/openai/v1` |
| `LLM_MODEL` | no | Defaults to `openai/gpt-oss-120b` |

### Run the tests

```bash
python -m pytest
```

The tests use invented data in the same shape as Qloo's, so they need no keys and make no network calls.

## How the code is laid out

| File | What it does |
|---|---|
| [app/qloo.py](app/qloo.py) | Qloo client: search, recommend, score. Rate limiting, retries, caching. |
| [app/llm.py](app/llm.py) | The plain-LLM baseline and its prompt |
| [app/compare.py](app/compare.py) | The two pipelines, and the rule for matching an LLM pick to a Qloo entity |
| [app/metrics.py](app/metrics.py) | The evaluation |
| [app/main.py](app/main.py) | Web endpoints, per-visitor rate limit, keepalive |
| [static/](static/) | The page: plain HTML, CSS and JavaScript, no build step |
| [tests/](tests/) | Unit tests for all of the above |
| [docs/qloo-api-notes.md](docs/qloo-api-notes.md) | What was confirmed against the live Qloo API |
| [BUILD_LOG.md](BUILD_LOG.md) | How this was built, in plain language |

## Deploying

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/anamankev/crossover)

The repo includes a [render.yaml](render.yaml) for Render's free plan. Click the button, sign in to Render, then enter `QLOO_API_KEY`, `LLM_API_KEY` and `REPO_URL` (this repo's address) when asked. Keys are never stored in the repo.

Render's free plan sleeps after 15 idle minutes. The app pings its own `/healthz` every 10 minutes to stay awake, a scheduled GitHub Action does the same from outside, and the page shows a "waking the server" message if a first request is slow. `/healthz` never calls Qloo, so staying awake costs no API quota.

## Limits

- Qloo's hackathon key allows 5 requests a second and 10,000 a month. One fresh comparison uses about 13. Responses are cached in memory for a day.
- Each visitor is limited to 30 comparisons per 10 minutes.
- LLM answers are cached for a day, so the same inputs show the same LLM list until the cache expires.

## License

[MIT](LICENSE)
