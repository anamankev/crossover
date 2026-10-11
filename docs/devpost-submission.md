# Crossover

**Tagline:** A taste check for AI agents. Same model, same question, and one of them can check its taste against Qloo.

**Live demo:** https://crossover-x9yy.onrender.com
**Code:** https://github.com/anamankev/crossover (MIT licensed)

**Links for judges**
- Pick blind, then see the labels: https://crossover-x9yy.onrender.com/?from=movie&q=Am%C3%A9lie&q=Before+Sunrise&q=Call+Me+by+Your+Name
- Straight to the numbers: https://crossover-x9yy.onrender.com/?from=movie&q=Mad+Max%3A+Fury+Road&q=Blade+Runner+2049&q=Dune&labels=1
- The taste-check API: https://crossover-x9yy.onrender.com/docs

## Inspiration

The brief says an agent should beat an LLM-only approach because it has Qloo. Nearly every entry will claim that. We wanted to show it in a way a judge can check in thirty seconds, and to be straight about what the evidence can and cannot prove.

There is also a real gap here. Anyone building a recommendation agent today ships an LLM's guesses straight to users. The model names things from memory with total confidence. Nothing checks whether a pick fits this person, or whether the thing even exists. Qloo can do that checking. Nobody had packaged it as a step an agent can call.

## What it does

You name three films and get music, or three artists and get films. **The same LLM answers twice.**

- **As an agent with Qloo.** It has three tools and decides how to use them. It asks Qloo's taste graph what people with this taste love. It thinks of its own ideas and has Qloo verify and score each one. Then it submits a final list, and it is only allowed to include picks Qloo has verified.
- **From memory.** The same model, the same inputs, no tools.

Then:

1. **You choose blind.** Both lists appear with no labels. You pick the one you would rather be handed. Only then does the page say which was which.
2. **You see the measurement.** Every pick on both sides is looked up in Qloo and scored against your inputs on the same scale: taste fit, how many picks could be verified, how mainstream each list is, how varied, and how much they overlap.
3. **You see what the agent did.** Which tools it called, which of its own ideas it checked, which it kept and which it dropped once it saw the score.

One real run: for Amélie, Before Sunrise and Call Me by Your Name, the agent took the graph's picks (Elliott Smith, Nick Drake, Sufjan Stevens), checked three ideas of its own, kept St. Vincent because Qloo scored it close to the top, and dropped Air and Françoise Hardy because it did not. The same model without Qloo opened with Yann Tiersen, the composer of the Amélie soundtrack, which is the obvious association and one of the lowest-scoring picks on the page.

## The taste check is a tool anyone's agent can call

The step that scores the plain LLM's list is a public endpoint, `POST /api/check`. Send what a person likes and what your agent wants to recommend. For each recommendation you get back whether Qloo can verify it exists, how well it fits, and better-fitting alternatives from the graph. It needs no LLM, and it is documented at `/docs` on the live site.

This is the part we think matters beyond the demo: a guardrail any recommendation agent can run before it shows a user anything.

## How Qloo is used

Qloo is in every step. Without it there is no product.

1. **Entity resolution** with `/search`, for the user's inputs and for every named pick.
2. **Cross-domain recommendation** with `/v2/insights`: film entities as `signal.interests.entities` with `filter.type` set to artists, or the reverse, with `feature.explainability` on.
3. **Scoring arbitrary entities** with `filter.results.entities`. Everything rests on this. We confirmed against the live API that affinity is an absolute score: an entity gets the same value in a top-12 list as when it is scored by request. That is what makes it fair to put any agent's picks on the same axis as Qloo's own.

## How we built it

- **Agent:** an LLM with tool calling (`openai/gpt-oss-120b` on Groq), three tools, and a loop we wrote ourselves. One rule lives in code and not in the prompt: a final pick must be an id that a Qloo tool returned. Anything else the model submits is thrown away, and the trace shows how many were discarded.
- **Backend:** Python and FastAPI. A small Qloo client handles the 5-per-second limit, retries and caching. When one model is rate limited the next is tried.
- **Frontend:** plain HTML, CSS and JavaScript, no build step. Light and dark themes.
- **Tests:** 122 unit tests. Every metric is checked against answers worked out by hand, and the agent is tested with a scripted model, including the cases where it tries to submit something Qloo never verified.
- **Hosting:** Render, with a keepalive so judges do not meet a sleeping server.

## The rule we held ourselves to

**Never show a number we could not compute.**

- If Qloo returns no score for a pick, it is shown as "not measured" and left out of the averages. It is not counted as zero.
- Qloo's search is fuzzy: it returned a real island for a made-up island name. So a pick is matched only when the name is identical and, for films, the release year agrees. Otherwise two different films called "Solaris" would be scored as one.
- Every metric says how many picks it is based on.
- If the LLM is unavailable, the page shows Qloo's direct answer and says so. It never fills a gap with invented results.

## What we learned

**Cross-domain quality depends on the pairing, so check before you build on it.** We first built films to travel. The API accepted it and returned confident scores. Then three European romance films came back as Beijing, Tianjin and Wuhan, and three desert science-fiction films came back as suburbs of Los Angeles. That looks like where fans live, not where they would want to go. A check built on that score would have declared Qloo the winner on results a person would find worse, so we threw the pairing out. Films and music, in both directions, were specific and convincing for every taste profile we tried. The full table is in the repo.

**Be honest about circular evidence.** Taste fit is Qloo's own score, and the agent chooses using it, so the agent leads on that row by design. The page says so next to the number. That is why the blind vote exists: people choosing between unlabelled lists is the one measure here that neither Qloo nor an LLM controls.

**An agent with a way to check itself changes its mind.** The most interesting thing in the trace is the agent dropping its own ideas after seeing how they score.

## Who it is for

- **Teams building recommendation agents.** `POST /api/check` is a guardrail to run before an agent shows a user anything: verify each pick, score it, swap in better fits.
- **People who love one art form and want a way into another,** with picks that a taste graph built from real behaviour stands behind.

## Limits we know about

- The blind-vote tally is kept in memory, so the on-page count restarts when the server is redeployed.
- Both sides run on a free open-weight model. A top commercial model may guess better from memory. The design holds either way, because both sides always use the same model.
- Only films and music are supported, for the reason above.

## What's next

- Offer the taste check as an MCP server, so any MCP-capable agent can add it with one URL.
- Store blind votes in a database and publish the running tally per pairing.
- Add domains as each one passes the same "do the results hold up" test that travel failed.
- Let the agent use more of Qloo: tag signals, popularity filters and audience data.

## Built with

Python, FastAPI, Qloo Insights API, Groq API (OpenAI-compatible tool calling), HTML, CSS, JavaScript, Render
