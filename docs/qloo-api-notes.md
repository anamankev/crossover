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
| `tags` | List of `{id, name, type}` | **no** — some books have none |
| `properties.geocode.country_code` | Country | destinations |
| `location.lat/lon` | Coordinates | destinations, places |
| `disambiguation` | Year / author / region | yes |

A bad entity id gives HTTP 400 with
`{"errors":[{"message":"...invalid entity ids","path":"signal.interests.entities"}]}`.

## Cross-domain works

This was the make-or-break question. Three films (Parasite, Spirited
Away, The Grand Budapest Hotel) as `signal.interests.entities`, with
`filter.type` set to a different domain each time:

| Output domain | Worked? | Sample results |
|---|---|---|
| `urn:entity:destination` | yes | Shinjuku, Naha, Kyoto, Tokyo, Kobe, Taipei |
| `urn:entity:book` | yes | Lord of the Flies, Brave New World, American Gods |
| `urn:entity:artist` | yes | Björk, Tom Waits, Jonny Greenwood, Sigur Rós |
| `urn:entity:podcast` | yes | Hidden Brain, Invisibilia, Radiolab |
| `urn:entity:tv_show` | yes | mostly Korean dramas |
| `urn:entity:brand` | yes | GoPro, Aston Martin, Rolex |
| `urn:entity:place` (needs `filter.location.query`) | yes, but noisy | hotels and landmarks mixed in |

Restaurants (`filter.type=urn:entity:place` plus
`filter.tags=urn:tag:genre:place:restaurant` plus a city) do return
results, but the list for Chicago included two hotels and a coffee shop
called "Chicago". Not clean enough to put in front of a judge yet, so
restaurants are parked.

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

## Explainability

With `feature.explainability=true` each result carries the share each
input contributed, for example Shinjuku: Spirited Away 0.38, Parasite
0.34, The Grand Budapest Hotel 0.28. The top-level `query.explainability`
gives the same breakdown averaged over the top 3 / 5 / 10 / all results.

## What this means for the metrics

| Metric | Field it needs | Verdict |
|---|---|---|
| Taste fit | `query.affinity` via `filter.results.entities` | Computable for both lists |
| Popularity | `popularity` | Computable for both lists |
| Variety (countries) | `properties.geocode.country_code` | Computable for destinations |
| Variety (genre tags) | `tags` | Computable, but only over picks that have tags; report how many |
| Overlap | `entity_id` | Computable |
| Catalog match rate | `/search` with strict name match | Computable |
| Novelty / serendipity / "accuracy" | would need user history or human ratings | **Not computable. Dropped.** |

## Call budget for one comparison

| Step | Calls |
|---|---|
| Look up 3 inputs | 3 |
| Qloo recommendations | 1 |
| Look up each LLM pick (8) | 8 |
| Score the LLM picks | 1 |
| **Total** | **13** |

## Terms worth remembering

The developer guide says private server-side caching is fine, but
**Qloo response data must not be stored in a public repository**. The
tests therefore use made-up fixtures in the same shape, never saved API
responses.

Do not use `/recommendations` or `/recs`; they are legacy and
unsupported for the hackathon.
