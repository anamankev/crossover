# Build log

A plain-language record of how Crossover was built on 11 October 2026,
what was decided and why. Written so you can explain every part of it
to a judge.

## The idea in one paragraph

You name three films you love and get music back, or three artists and
get films back. Two things answer: Qloo's taste graph, and a plain LLM
working from memory. Then the app asks Qloo to score the LLM's picks
against your three inputs, and shows both lists and the numbers side by
side. The measuring is the point of the project.

## Words you will need

| Word | What it means here |
|---|---|
| **API** | A way for one program to ask another for data over the internet. We ask Qloo's. |
| **Endpoint** | One specific address on an API. Qloo's `/search` and `/v2/insights` are endpoints. So are our own `/api/grounded` and `/api/baseline`. |
| **API key** | A password that identifies us to an API. Kept in `.env`, never in the code. |
| **Entity** | One thing in Qloo's catalog: a film, an artist. Each has an id. |
| **Signal** | What you tell Qloo the person likes. We send three entity ids. |
| **Affinity** | Qloo's score, 0 to 1, for how well something fits the signals. |
| **FastAPI** | The Python library that turns our functions into a web server. |
| **Virtual environment (`.venv`)** | A private folder of Python libraries for this project only. |
| **Test** | A small program that checks another piece of code gives the expected answer. |
| **Commit / push** | Commit saves a snapshot of the code. Push sends the snapshots to GitHub. |
| **Deploy** | Put the app on a server so anyone can open it. We use Render. |

## What happened, in order

### 1. Secrets first

Created `.gitignore` containing `.env` and committed it **before**
creating `.env`. The repo has to be public, and anything ever committed
stays in the history forever, so the ignore rule had to exist before the
key did. `git check-ignore .env` confirmed the rule works. The first
commit in the history is that single file.

### 2. Learning the Qloo API by using it

Read the hackathon developer guide, then made real calls. Everything
confirmed is in `docs/qloo-api-notes.md`. The findings that shaped the
project:

- The key goes in a header called `X-Api-Key`.
- Limits: 5 calls a second, 10,000 a month.
- **Cross-domain works**: send films as signals, ask for artists back.
- **Affinity is an absolute score.** Kyoto scored 0.9231 in a top-8 list
  and 0.9231 again when we asked Qloo to score it by name. That is what
  lets us score the LLM's picks on the same scale as Qloo's.
- **Search is fuzzy.** Searching for the made-up "Zxqv Flarnbuckle
  Island" returned "Hilton Head Island". A search hit is not proof that
  a name is real.

### 3. Walking skeleton

A FastAPI app with one page saying hello and a `/healthz` check, plus
`render.yaml`, the file that tells Render how to run it. It ran locally
on the first try.

**Deployment friction found, as you wanted:** this Mac had no GitHub
login, no `gh` tool, no SSH key, and no Render account connected. Git
could commit but had nowhere to push. I installed GitHub's official
`gh` tool into `~/.local/bin` (checksum verified) and started a browser
sign-in, which needs you to type a one-time code. Until that is done the
code lives only on this computer.

### 4. Qloo client (`app/qloo.py`)

Three things the app can ask Qloo:

- `search(name)`: find a name in the catalog.
- `recommend(seeds)`: the top 8 fits in the other domain.
- `score(seeds, entities)`: how well *these specific* entities fit.

It also waits its turn so we never exceed 5 calls a second, retries if
Qloo says "too many requests", and remembers answers for a day so repeat
visitors do not use up the monthly quota.

### 5. The LLM baseline (`app/llm.py`)

One plain question to Claude Opus 5.5, no tools and no Qloo data: here
are three things a person loves, recommend eight in the other domain.
The prompt does not push it toward famous or obscure picks, and the page
shows the exact prompt for every run. A strong model at its normal
effort was chosen on purpose: beating a weak baseline would prove
nothing.

If there is no LLM key, the app says the baseline is not connected. It
never shows invented results.

### 6. Metrics (`app/metrics.py`)

Four numbers, each a few lines of arithmetic on fields Qloo returned:

| Metric | In plain words | The sum |
|---|---|---|
| Taste fit | How well do the picks fit your inputs? | Average of the affinity scores |
| How mainstream | Is the list leaning on the obvious? | The middle popularity value |
| Variety | How different are the picks from each other? | For every pair: 1 minus (shared genres ÷ all genres of the two). Then average. |
| Agreement | Did both sides pick the same things? | Count the ids in both lists |

Every metric also reports how many picks it used, so the screen can say
"based on 7 of 8 picks". The tests in `tests/test_metrics.py` check each
one against answers worked out by hand, with the working written in the
comments.

### 7. The comparison pipeline (`app/compare.py`)

This is where the LLM's picks get checked. For each pick:

1. Search Qloo for the name. Keep only results whose name is *exactly*
   the same once capitals, accents and punctuation are ignored.
2. Ask Qloo to score all of those in one call.
3. For a film, keep the one whose release year matches the LLM's year,
   give or take one. For an artist, keep the most popular one with that
   name.
4. If nothing survives, the pick is shown as "not measured" with the
   reason, and it is left out of every number.

### 8. The page (`static/`)

Plain HTML, CSS and JavaScript, no build step. The measurement panel is
at the top, then the two lists. Blue always means Qloo and orange always
means the LLM. The Qloo side appears first because it is fast; the LLM
side fills in a few seconds later. Each comparison gets its own link you
can share.

## Decisions that changed the plan

### Films and music instead of restaurants, travel or books

**This is the biggest call made today, and it is yours to overrule.**

The brief named restaurants, travel and books as the output domains. I
built travel first and it worked technically. Then I tried more
examples:

| You love | Qloo's travel answer |
|---|---|
| Amélie, Before Sunrise, Call Me by Your Name | Beijing, Shanghai, Tianjin, Wuhan |
| Mad Max: Fury Road, Blade Runner 2049, Dune | Santa Clarita, Pasadena, Burbank, Oxnard |

Those look like where fans of the films *live*, not where they would
want to go. A plain LLM would say Paris and Vienna, and any person would
prefer that. But our taste-fit metric would still have put Qloo ahead,
because the metric is Qloo's own score. That is the "plausible but
wrong" result you said was the worst possible outcome.

Books came back as the same famous classics for unrelated inputs.
Restaurants came back as a city's tourist spots. Films to music and
music to films came back specific and convincing every time, for
example:

| You love | Qloo's answer |
|---|---|
| Amélie, Before Sunrise, Call Me by Your Name | Elliott Smith, Nick Drake, Sufjan Stevens, Belle and Sebastian |
| Mad Max: Fury Road, Blade Runner 2049, Dune | Trent Reznor and Atticus Ross, Hildur Guðnadóttir, Jóhann Jóhannsson, Brian Eno |
| Radiohead, Björk, Frank Ocean | Lost in Translation, Synecdoche New York, Magnolia, Punch-Drunk Love |

So the app ships one pairing, in both directions, where the score means
what the page says it means. The full evidence table is in
`docs/qloo-api-notes.md`.

### Median popularity, not average

Every well-known artist has a popularity between 0.99 and 1.0. Averages
of those are indistinguishable. The median, shown as "top 0.07%", is
readable and is not thrown off by one obscure pick.

### No metric built on "which input drove each pick"

Qloo returns how much each of your three inputs contributed to each
pick. I planned a metric on it. The real numbers turned out to be nearly
equal for every pick (about 37% / 33% / 30%), even for off-taste ones, so
a metric would have looked meaningful and said nothing. The shares are
shown under each Qloo pick as returned, and that is all.

### Matching by name **and** year

Found while testing: "Solaris" is two films, and a search for "Venice"
returns two American towns. Matching on name alone would have scored the
wrong thing and shown a confident, wrong number.

## The honest limits, in the words to use with a judge

- "Taste fit is Qloo's own score, and Qloo's list is sorted by it, so
  Qloo leads on that row by design. We say so on the page. What it shows
  is how far the LLM's picks are from the best available, and which of
  them the graph agrees with."
- "We cannot say which list a person would enjoy more. That needs people
  rating the results."
- "When Qloo cannot score a pick, we show it as not measured. We do not
  count it as zero and we do not guess."

## State at the end of the day

| Thing | State |
|---|---|
| Qloo side, both directions | Working against the live API |
| Metrics | Working, unit tested |
| Page | Working locally in light, dark and phone widths |
| Tests | 86 passing |
| LLM baseline | Code written and tested with a pretend LLM. **Never run against the real LLM: there is no key in `.env` yet.** |
| GitHub | **Not pushed.** Waiting on your sign-in. |
| Render | **Not deployed.** Needs the GitHub repo first, then your Render account. |
| Public URL | **None yet.** |

## What to do next

See the list at the end of the hand-off message from the session. In
short: sign in to GitHub, add the LLM key, connect Render.
