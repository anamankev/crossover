# Build log

A plain-language record of how Crossover was built on 11 October 2026,
what was decided and why. Written so you can explain every part of it
to a judge.

## The idea in one paragraph

You name three films you love and get music back, or three artists and
get films back. The same LLM answers twice: once from memory, and once
as an agent that can ask Qloo's taste graph and check its own ideas
against it. You pick the list you prefer without knowing which is which.
Then the page shows the labels, the numbers, and what the agent did.
The checking step is also offered as a tool other people's agents can
call. The measuring is the point of the project.

## Words you will need

| Word | What it means here |
|---|---|
| **API** | A way for one program to ask another for data over the internet. We ask Qloo's. |
| **Endpoint** | One specific address on an API. Qloo's `/search` and `/v2/insights` are endpoints. So are our own `/api/compare` and `/api/check`. |
| **API key** | A password that identifies us to an API. Kept in `.env`, never in the code. |
| **Entity** | One thing in Qloo's catalog: a film, an artist. Each has an id. |
| **Signal** | What you tell Qloo the person likes. We send three entity ids. |
| **Affinity** | Qloo's score, 0 to 1, for how well something fits the signals. |
| **FastAPI** | The Python library that turns our functions into a web server. |
| **Virtual environment (`.venv`)** | A private folder of Python libraries for this project only. |
| **Agent** | An LLM that is given tools (functions it may call) and decides for itself which to use and when. |
| **Tool call** | The LLM replying "run this function with these inputs" instead of plain text. Our code runs it and sends the result back. |
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
sign-in, which needs you to type a one-time code. Two codes expired
before the third was approved, and the repo was pushed after that.

### 4. Qloo client (`app/qloo.py`)

Three things the app can ask Qloo:

- `search(name)`: find a name in the catalog.
- `recommend(seeds)`: the top 8 fits in the other domain.
- `score(seeds, entities)`: how well *these specific* entities fit.

It also waits its turn so we never exceed 5 calls a second, retries if
Qloo says "too many requests", and remembers answers for a day so repeat
visitors do not use up the monthly quota.

### 5. The LLM baseline (`app/llm.py`)

One plain question to an LLM, no tools and no Qloo data: here are three
things a person loves, recommend eight in the other domain. The prompt
does not push it toward famous or obscure picks, and the page shows the
exact prompt and the model name for every run.

This was first written for Claude Opus 5.5. You asked for a free key, so
it now uses the "OpenAI-compatible" format that many providers accept,
and defaults to `openai/gpt-oss-120b` on Groq's free tier (no credit
card, roughly 30 requests a minute and 1,000 a day). Google's free tier
was the other candidate, but recent reports put it at about 20 requests
a day, which a few judges could use up.

**The trade-off to be ready for:** a free open-weight model is a weaker
baseline than a top paid one. A judge could say "you only beat a free
model". The README says this plainly. Switching to a stronger model
later is two settings (`LLM_BASE_URL` and `LLM_MODEL`), no code.

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

### 9. Second build, same day: a real agent

After the first version was live, I checked it against the judging
criteria and it was weak in two places: the rules ask for an agent and
it was a fixed pipeline, and "film fans get music picks" is not a real
problem for a real audience. You said to build the best version, so:

**The Qloo side became a real agent (`app/agent.py`).** The LLM gets
three tools and chooses how to use them:

| Tool | What it does |
|---|---|
| `get_taste_graph_picks` | Asks Qloo what people with this taste love |
| `check_my_ideas` | Has Qloo verify and score the LLM's own ideas |
| `submit_picks` | Gives the final answer |

The one hard rule is in code, not in the prompt: a final pick must be
an id that a tool returned, meaning Qloo has verified it. Anything else
is thrown away and the page says how many were discarded. In the first
live run, the agent checked "Air", "Françoise Hardy" and "St. Vincent",
kept St. Vincent (fit 0.911) and dropped the other two (0.867, 0.853).

**Both sides now use the same model.** That makes it a clean
experiment: one thing differs, access to Qloo. It also answers "you only
beat a free model", because the free model is on both sides.

**The check became a public tool (`POST /api/check`).** Any agent can
send "this person likes X, I want to recommend Y" and get back whether
each Y is real and how well it fits. This is the product for a real
audience: teams building recommendation agents.

**A blind vote was added.** The page shows both lists unlabelled and
asks which you would take, before showing anything else. This matters
because every other number on the page comes from Qloo, and the agent
picks using Qloo's score, so it wins those by design. People choosing
blind is the only evidence here that Qloo does not control.

**Model fallback.** Groq's free tier gives each model its own allowance
(1,000 requests a day, 8,000 tokens a minute). If one is rate limited
the next is tried, and the page shows which model answered each side.

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

- "Taste fit is Qloo's own score, and the agent chooses using it, so
  the agent leads on that row by design. We say so on the page. It shows
  how far an unaided LLM lands from what the graph supports."
- "The independent evidence is the blind vote. So far that is a small
  number of votes, and the count restarts when the server is redeployed."
- "Both sides run on the same free open-weight model. A stronger model
  might guess better from memory. The comparison stays fair because both
  sides always use the same one."
- "When Qloo cannot score a pick, we show it as not measured. We do not
  count it as zero and we do not guess."

## State at the end of the day

| Thing | State |
|---|---|
| Agent with Qloo, both directions | Working against the live Qloo API and the real LLM |
| Plain-LLM baseline | Working against the real LLM |
| Taste-check tool (`/api/check`) | Working |
| Blind vote | Working. Tally is in memory and in the server log; it restarts on redeploy. |
| Metrics | Working, unit tested |
| Tests | 122 passing, locally and on GitHub |
| GitHub | Public at https://github.com/anamankev/crossover |
| Public URL | https://crossover-x9yy.onrender.com |

## What to do next

1. **Submit on Devpost.** Join the hackathon at https://qloo.devpost.com
   and paste in `docs/devpost-submission.md`. Add screenshots of the
   blind choice and of the measurement. Judges may score from the text
   and images alone. Submit early; it can be edited until 30 October.
2. **Collect blind votes.** Send the link to friends and classmates. A
   real tally is the strongest evidence this project can have.
3. **Give the votes a permanent home.** A free database (Neon or
   Upstash) would stop the tally restarting. About 30 minutes of work.
4. **Offer the taste check as an MCP server**, so an agent can add it
   with one URL.
5. **Check the page on a real phone.**
6. **Watch the quotas during judging (2 to 16 November).** Qloo allows
   10,000 calls a month and Groq 1,000 requests a day per model.
