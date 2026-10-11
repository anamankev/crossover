# Crossover

**Tagline:** Name three films you love, get the music that fits. Then watch a taste graph and an LLM answer the same question, with the difference measured on screen.

**Live demo:** [LIVE URL]
**Code:** [GITHUB URL] (MIT licensed)

## Inspiration

The brief for this hackathon says an agent should outperform an LLM-only approach because it has Qloo. Almost every project will say that it does. We wanted to show it, with numbers a judge can check, and to be straight about what those numbers can and cannot prove.

Taste that crosses domains is a good place to test this. "I love Amélie and Before Sunrise, what should I listen to?" is a question an LLM will happily answer from memory. It is also exactly the kind of question a taste graph built from real behaviour should answer better.

## What it does

You name three films and get music, or three artists and get films.

Two things answer:

- **The Qloo-grounded agent** finds your three inputs in Qloo's catalog, asks the taste graph for the best fits in the other domain, and reports how much each of your inputs contributed to each pick.
- **A plain LLM** ([MODEL NAME AS SHOWN ON THE LIVE PAGE]) gets the same three inputs and the same request, with no access to Qloo.

Then Crossover does the part most demos skip. It looks up every one of the LLM's picks in Qloo's catalog and asks Qloo to score them against your three inputs, on the same scale as Qloo's own picks. The page shows both lists side by side with:

- **Taste fit:** average Qloo affinity for each list, plus a dot for every single pick so you can see which LLM guesses the graph agrees with and which miss.
- **How mainstream:** where the typical pick in each list ranks by popularity.
- **Variety:** how different the picks in each list are from one another, from Qloo's genre tags.
- **Agreement:** how many picks the two lists share, and how many of the LLM's picks could be verified at all.

Every comparison has its own link, so a result can be shared.

## What makes it Qloo-powered

Without Qloo there is no product. Qloo is used three ways in every request:

1. **Entity resolution** with `/search`, for the user's inputs and again for each LLM pick.
2. **Cross-domain recommendation** with `/v2/insights`: film entities as `signal.interests.entities`, `filter.type` set to artists, or the reverse, with `feature.explainability` switched on.
3. **Scoring arbitrary entities** with `filter.results.entities`. This is the piece the whole evaluation rests on. We confirmed against the live API that affinity is an absolute score: an entity gets the same value in a top-8 list as when it is scored by request. That is what makes it fair to put the LLM's picks on the same axis as Qloo's.

## How we built it

- **Backend:** Python and FastAPI. A small Qloo client handles the 5-per-second rate limit, retries and caching. The metrics are a separate module of plain arithmetic.
- **Frontend:** plain HTML, CSS and JavaScript with no build step. Light and dark themes, works on a phone.
- **Tests:** 92 unit tests. Each metric is tested against answers worked out by hand.
- **Hosting:** Render, with a keepalive so judges do not hit a sleeping server.

We started by spending an hour only making real API calls and writing down what came back. Those notes are in the repo.

## The rule we held ourselves to

**Never show a number we could not compute.**

- If Qloo returns no score for a pick, the pick is shown as "not measured" and left out of the averages. It is not counted as zero.
- Qloo's search is fuzzy: it returned a real island for a made-up island name. So an LLM pick is only matched when the name is identical and, for films, the release year agrees. Otherwise two different films called "Solaris" would be scored as one.
- Every metric says how many picks it is based on.
- We planned a metric on Qloo's explainability shares and dropped it when the real values turned out nearly equal for every pick. It would have looked informative and meant nothing.

## What we learned

**Cross-domain quality depends on the pairing, and it is worth checking before you build on it.** We first built films to travel. The API accepted it and returned confident scores. Then three European romance films came back as Beijing, Tianjin and Wuhan, and three desert science-fiction films came back as suburbs of Los Angeles. That looks like where fans live, not where they would want to travel. Our own metric would still have declared Qloo the winner, because the metric is Qloo's score. We threw that pairing out. Films and music, in both directions, gave specific, convincing results for every taste profile we tried, so that is what shipped. The full comparison of pairings is in the repo.

**The honest framing is stronger than the easy one.** Taste fit is Qloo's own score and Qloo's list is ranked by it, so Qloo leads on that row by construction. We say so on the page, next to the number. What the measurement really shows is how far an LLM's guesses sit from the best available answer, and which guesses the graph backs.

## What it cannot tell you

Whether you would actually enjoy a pick. That needs people rating results. Crossover measures agreement with a taste graph built from real behaviour, not enjoyment.

## Who it is for

- **People who love one art form and want a way into another**, with a reason for each suggestion that is more than a sentence an LLM wrote.
- **Teams building recommendation agents.** The scoring technique here, checking an LLM's suggestions against Qloo with `filter.results.entities` before showing them, works as a guardrail for any agent that recommends things.

## What's next

- Let the agent decide which Qloo calls to make, including tag signals and popularity filters, and expose the scoring check as a tool other agents can call.
- Ask the LLM twice and measure how much its own answer changes between runs. Qloo's does not.
- Add domains as each one passes the same "do the results hold up" check that travel failed.
- Collect ratings from real people, so the comparison has a referee that is neither Qloo nor an LLM.

## Built with

Python, FastAPI, Qloo Insights API, Groq API, HTML, CSS, JavaScript, Render
