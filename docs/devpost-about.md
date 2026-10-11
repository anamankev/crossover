## Try it

- **Pick blind, then see the labels:** [Amélie, Before Sunrise, Call Me by Your Name → music](https://crossover-x9yy.onrender.com/?from=movie&q=Am%C3%A9lie&q=Before+Sunrise&q=Call+Me+by+Your+Name)
- **Straight to the numbers:** [Mad Max: Fury Road, Blade Runner 2049, Dune → music](https://crossover-x9yy.onrender.com/?from=movie&q=Mad+Max%3A+Fury+Road&q=Blade+Runner+2049&q=Dune&labels=1)
- **The taste-check API for your own agent:** [interactive docs](https://crossover-x9yy.onrender.com/docs)

If the first load is slow, the free server is waking up. Give it about 30 seconds.

## Inspiration

The brief says an agent should beat an LLM-only approach because it has Qloo. Nearly every entry will claim that. We wanted to show it in a way a judge can check in thirty seconds, and to be straight about what the evidence can and cannot prove.

There is also a real gap. Anyone building a recommendation agent today ships an LLM's guesses straight to users. The model names things from memory with total confidence. Nothing checks whether a pick fits this person, or whether the thing even exists. Qloo can do that checking, and nobody had packaged it as a step an agent can call.

## What it does

You name three films and get music, or three artists and get films. **The same LLM answers twice.**

- **As an agent with Qloo.** It has three tools and decides how to use them. It asks Qloo's taste graph what people with this taste love. It thinks of its own ideas and has Qloo verify and score each one. Then it submits a final list, and it may only include picks Qloo has verified.
- **From memory.** The same model, the same inputs, no tools.

Then:

1. **You choose blind.** Both lists appear with no labels. You pick the one you would rather be handed. Only then does the page say which was which.
2. **You see the measurement.** Every pick on both sides is looked up in Qloo and scored against your inputs on the same scale: taste fit, how many picks could be verified, how mainstream each list is, how varied, and how much they overlap.
3. **You see what the agent did.** Which tools it called, which of its own ideas it checked, which it kept and which it dropped once it saw the score.

One real run: for Amélie, Before Sunrise and Call Me by Your Name, the agent took the graph's picks (Elliott Smith, Nick Drake, Sufjan Stevens), checked three ideas of its own, kept St. Vincent because Qloo scored it close to the top, and dropped Air and Françoise Hardy because it did not. The same model without Qloo opened with Yann Tiersen, who composed the Amélie soundtrack. That is the obvious association, and it was one of the lowest-scoring picks on the page.

**The taste check is also a tool anyone's agent can call.** `POST /api/check` takes what a person likes and what your agent wants to recommend. For each recommendation it returns whether Qloo can verify it exists, how well it fits, and better-fitting alternatives from the graph. It is a guardrail to run before an agent shows a user anything.

## How we built it

- **Qloo, in every step.** `/search` resolves names to entities. `/v2/insights` gives cross-domain recommendations, with film entities as `signal.interests.entities` and `filter.type` set to artists, or the reverse. `filter.results.entities` scores any named pick against the same inputs, and `feature.explainability` shows what drove each pick.
- **The agent.** An LLM with tool calling (`openai/gpt-oss-120b` on Groq), three tools, and a loop we wrote ourselves. One rule lives in code and not in the prompt: a final pick must be an id that a Qloo tool returned. Anything else the model submits is thrown away, and the trace shows how many were discarded.
- **Backend.** Python and FastAPI. A small Qloo client handles the 5-per-second limit, retries and caching.
- **Frontend.** Plain HTML, CSS and JavaScript with no build step.
- **Tests.** 123 unit tests. Every metric is checked against answers worked out by hand.

We started by spending an hour only making real API calls and writing down what came back. Those notes are in the repo.

## Challenges we ran into

- **Our first pairing gave confident, wrong-looking answers.** We built films to travel first. Three European romance films came back as Beijing, Tianjin and Wuhan, and three desert science-fiction films came back as suburbs of Los Angeles. That looks like where fans live, not where they would want to go. A check built on that score would have declared Qloo the winner on results a person would find worse, so we threw the pairing out. Films and music, in both directions, held up for every taste profile we tried.
- **Qloo's search is fuzzy.** It returned a real island for a made-up island name. Matching an LLM's pick by name alone would have scored the wrong thing, so a pick counts only when the name is identical and, for films, the release year agrees. Otherwise two different films called "Solaris" would be scored as one.
- **The main metric is circular.** Taste fit is Qloo's own score and the agent chooses using it, so the agent leads on that row by design. We say so on the page, next to the number, and we added the blind vote because people choosing between unlabelled lists is the one measure here that neither Qloo nor an LLM controls.
- **Popularity bunches up near the top.** Every well-known artist sits between the 99th and 100th percentile, so averages were meaningless. We report the median and show it as "top 0.07%".
- **Free-tier limits.** Each free model allows 8,000 tokens a minute, so the agent's tool output is kept compact and a second model takes over when the first is rate limited.

## Accomplishments that we're proud of

- **We never show a number we could not compute.** If Qloo returns no score for a pick, it is shown as "not measured" and left out of the averages. It is not counted as zero. Every metric says how many picks it is based on.
- **A clean experiment.** Both sides use the same model and the same inputs. The only difference is access to Qloo.
- **The agent changes its mind.** The trace shows it dropping its own ideas after seeing how they score.

## What we learned

- **Cross-domain quality depends on the pairing.** The API will accept a pairing and return confident scores even when the results do not mean what you hope. Check before you build on it.
- **Affinity is an absolute score.** An entity gets the same value in a top-12 list as when it is scored by request. That one fact is what makes it fair to put any agent's picks on the same axis as Qloo's own.
- **Honest framing is stronger than easy framing.** Saying where the agent wins by design made the rest of the evidence more believable.

## What's next for Crossover

- Offer the taste check as an MCP server, so any MCP-capable agent can add it with one URL.
- Store blind votes in a database and publish the running tally. Today the count restarts when the server is redeployed.
- Run both sides on a top commercial model and report whether the gap holds.
- Add domains as each one passes the same "do the results hold up" test that travel failed.
