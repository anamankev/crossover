# Qloo API notes

Everything here was confirmed with real calls to the hackathon server on
2026-10-11, not copied from the docs. Where the docs and the server
disagreed, the server wins.

## Basics

| Thing | Value |
|---|---|
| Base URL | `https://hackathon.api.qloo.com` (hackathon keys fail with 401 anywhere else) |
| Auth | Header `X-Api-Key: <key>`. Not a Bearer token, not a query parameter. |
| Method | `GET`, parameters in the query string |
| Rate limit | **5 requests per second**, **10,000 per month** (from the `x-second-ratelimit-*` and `x-month-ratelimit-*` response headers) |
| Over the limit | HTTP 429, body `{"error_msg":"Rate limit exceeded"}`, header `x-second-ratelimit-reset` says how many seconds to wait |

The monthly limit is the one that matters. One comparison costs roughly
13 calls (see "Call budget" below), so the quota covers about 750
uncached comparisons a month. The app caches every Qloo response in
memory and the keepalive ping never touches Qloo.

## The two endpoints we use

### `GET /search` — turn a name into an entity

```
/search?query=Parasite&types=urn:entity:movie&take=3
```

Returns `{"results": [ ... ]}`. Each result has `name`, `entity_id`,
`types`, `popularity`, `disambiguation` (year for films, "year, author"
for books), `properties`, `tags`.

Two traps:

1. **Search is fuzzy and always tries to return something.** Searching
   destinations for the nonsense name "Zxqv Flarnbuckle Island" returned
   "Hilton Head Island". So a search hit is not proof the name exists.
   We only accept a hit whose name matches the query after
   normalising case, accents and punctuation.
2. **Without `types`, results mix domains.** "Kyoto" returns a
   restaurant in Israel first, then the city, then an album. Always
   pass `types`.

`popularity` is sometimes missing from a search result.

### `GET /v2/insights` — recommendations from the taste graph

```
/v2/insights?filter.type=urn:entity:destination
            &signal.interests.entities=<id>,<id>,<id>
            &take=8
            &feature.explainability=true
```

Returns `{"success": true, "results": {"entities": [ ... ]}}`.
Each entity has:

| Field | Meaning | Always there? |
|---|---|---|
| `name`, `entity_id` | | yes |
| `popularity` | Percentile, 0 to 1. 0.98 means more popular than 98% of its category. | yes in insights results |
| `query.affinity` | How strongly this entity fits the input signals, 0 to 1 | yes |
| `query.explainability` | How much each input entity contributed to this pick | only with `feature.explainability=true` |
| `tags` | List of `{id, name, type}` | **no**: some entities have none |
| `properties.release_year` | Release year | films |
| `properties.image.url` | Poster or photo | films, artists, books |
| `disambiguation` | Year, or the name again for artists | yes |

A bad entity id gives HTTP 400 with
`{"errors":[{"message":"...invalid entity ids","path":"signal.interests.entities"}]}`.

## Cross-domain works, but not equally well everywhere

This was the make-or-break question. Entities from one domain go in as
`signal.interests.entities`, and `filter.type` asks for a different
domain back. The API accepts this for almost every pairing. Whether the
*answers are any good* depends heavily on the pairing, so each one was
tried with several clear taste profiles.

| Pairing | What came back | Verdict |
|---|---|---|
| Films → Music | Amélie, Before Sunrise, Call Me by Your Name → Elliott Smith, Nick Drake, Sufjan Stevens, Belle and Sebastian. Mad Max, Blade Runner 2049, Dune → Trent Reznor and Atticus Ross, Hildur Guðnadóttir, Jóhann Jóhannsson, Brian Eno | **Excellent. Shipped.** |
| Music → Films | Radiohead, Björk, Frank Ocean → Lost in Translation, Synecdoche New York, Magnolia, Punch-Drunk Love. Johnny Cash, Dolly Parton, Willie Nelson → Walk the Line, Crazy Heart | **Excellent. Shipped.** |
| Films → Travel | Amélie, Before Sunrise, Call Me by Your Name → Beijing, Shanghai, Tianjin, Wuhan. Mad Max, Blade Runner 2049, Dune → Santa Clarita, Pasadena, Burbank, Oxnard | The score seems to track where fans live, not where they would want to go. Left out. |
| Films → Books | Brave New World, 1984, Fahrenheit 451 and Flowers for Algernon turned up for unrelated film sets. One set returned six volumes of One Piece. | Generic or noisy. Left out. |
| Music → Books | Mostly biographies of unrelated musicians (Eric Clapton, John Lennon) | Left out. |
| Films → Restaurants in a city (`urn:entity:place` + `filter.location.query` + restaurant tag) | Chicago: Giordano's, Navy Pier, hotel restaurants, a coffee shop named "Chicago" | The city's tourist favourites regardless of taste. Left out. |
| Books → Travel | HTTP 400: "All of the provided signal.interests.entities were invalid" for every book id tried | Not supported. |

Why this matters for the project: the app measures a plain LLM against
Qloo's affinity score. That is only fair where the score means "people
with this taste also love this". For travel it appears to mean
something closer to "people with this taste are concentrated here", and
a comparison built on it would have declared Qloo the winner on results
a person would find worse. So only the film and music pairings ship.

## The finding the evaluation is built on

**Affinity is an absolute score, not a ranking within the response.**

Kyoto scored `0.9230544` in the open top-8 query. Then we asked Qloo to
score a hand-picked list using `filter.results.entities`:

```
/v2/insights?filter.type=urn:entity:destination
            &signal.interests.entities=<3 film ids>
            &filter.results.entities=<Kyoto, Paris, Las Vegas, Reykjavik, Cancún ids>
```

| Destination | Affinity to the three films |
|---|---|
| Kyoto | 0.9231 (same as before) |
| Reykjavik | 0.7762 |
| Las Vegas | 0.7312 |
| Paris | 0.7153 |
| Cancún | 0.6392 |

So we can take whatever a plain LLM suggests, look each name up, and ask
Qloo how well those exact picks fit the same three inputs. That number is
measured, not estimated.

The same check with artists: scored against Amélie, Before Sunrise and
Call Me by Your Name, Sufjan Stevens came back at 0.951, The National
0.930, Yann Tiersen 0.831 and Metallica 0.770. All seven artists asked
about were returned.

## Explainability

With `feature.explainability=true` each result carries the share each
input contributed, for example Shinjuku: Spirited Away 0.38, Parasite
0.34, The Grand Budapest Hotel 0.28. It also works on the scoring call. The top-level `query.explainability`
gives the same breakdown averaged over the top 3 / 5 / 10 / all results.

## More traps found while building

- **The same entity has a different `popularity` in `/search` and in
  `/v2/insights`.** Reykjavik was 0.892 in search and 0.916 in insights.
  Every metric therefore reads its fields from insights responses only,
  for both lists.
- **Popularity bunches up near 1.0 for anything well known.** Elliott
  Smith is 0.9994, Metallica is 1.0000. An average hides everything, so
  the app reports the median and shows it as "top 0.07%".
- **Some catalog entries cannot be scored.** The destination "Seoul"
  exists in search but the scoring call returns nothing for it. Such a
  pick is shown as "not measured", never as zero.
- **Names are not unique.** "Solaris" is a 1972 film and a 2002 film;
  search for "Venice" returns two American towns and not the Italian
  city. Matching a film needs the title and the release year.
- **Explainability shares are close to equal.** For three inputs they
  come back near 0.37 / 0.33 / 0.30 for almost every pick, including
  off-taste ones. They are shown as returned, and no metric is built on
  them.
- **Film entities carry `properties.release_year`.** Their
  `disambiguation` is "2019" in search but "2017, Greta Gerwig" in
  insights, so the year is read from the property.

## What this means for the metrics

| Metric | Field it needs | Verdict |
|---|---|---|
| Taste fit (mean affinity) | `query.affinity` via `filter.results.entities` | Computable for both lists |
| How mainstream (median popularity) | `popularity` from insights | Computable for both lists |
| Variety (genre tags) | `tags` whose type starts with `urn:tag:genre` | Computable; picks without genre tags are left out and counted |
| Agreement (overlap) | `entity_id` | Computable |
| Coverage (how many LLM picks could be checked) | `/search` exact name, plus `release_year` for films | Computable |
| Blend across inputs | `query.explainability` | Field exists but is nearly flat. **Dropped.** |
| Novelty, serendipity, "accuracy" | would need user history or human ratings | **Not computable. Dropped.** |

## Call budget for one comparison

| Step | Calls |
|---|---|
| Look up 3 inputs | 3 |
| Qloo recommendations | 1 |
| Look up each LLM pick (8) | 8 |
| Score the LLM picks | 1 |
| **Total** | **13** |

Repeat requests are served from an in-memory cache and cost nothing.

## Terms worth remembering

The developer guide says private server-side caching is fine, but
**Qloo response data must not be stored in a public repository**. The
tests therefore use made-up fixtures in the same shape, never saved API
responses.

Do not use `/recommendations` or `/recs`; they are legacy and
unsupported for the hackathon.
