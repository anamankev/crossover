"""The domains a user can start from and cross over into.

`qloo_type` is the value Qloo expects in `filter.type` and in the
`types` parameter of /search.

Only films and music are offered. Travel, books and restaurants were
tested as outputs and left out: see docs/qloo-api-notes.md for what
Qloo returned and why it was not good enough to ship.
"""

DOMAINS = {
    "movie": {
        "label": "Films",
        "qloo_type": "urn:entity:movie",
        "noun": "films",
        # How the plain LLM is told to name its picks, so each one can be
        # looked up in Qloo afterwards.
        "naming": "Give each film's title and the year it was released.",
        # Two films can share a title, so a match also needs the year.
        "needs_year": True,
    },
    "artist": {
        "label": "Music",
        "qloo_type": "urn:entity:artist",
        "noun": "music artists",
        "naming": "Give each artist or band name only.",
        "needs_year": False,
    },
}


def allowed_pair(input_domain: str, output_domain: str) -> bool:
    """A crossover has to actually cross into a different domain."""
    return input_domain in DOMAINS and output_domain in DOMAINS and input_domain != output_domain
