"""The domains a user can start from and cross over into.

`qloo_type` is the value Qloo expects in `filter.type` and in the
`types` parameter of /search.
"""

INPUT_DOMAINS = {
    "movie": {"label": "Films", "qloo_type": "urn:entity:movie", "noun": "films"},
    "artist": {"label": "Music", "qloo_type": "urn:entity:artist", "noun": "music artists"},
    "book": {"label": "Books", "qloo_type": "urn:entity:book", "noun": "books"},
}

OUTPUT_DOMAINS = {
    "destination": {
        "label": "Travel",
        "qloo_type": "urn:entity:destination",
        "noun": "travel destinations",
        # How the plain LLM is told to name its picks, so each one can be
        # looked up in Qloo afterwards and checked against its country.
        "naming": (
            "Give each as a city, town or region name only (for example \"Lisbon\"), "
            "plus its two-letter ISO country code (for example \"PT\")."
        ),
    },
}


def allowed_pair(input_domain: str, output_domain: str) -> bool:
    """A crossover has to actually cross: books to books is not allowed."""
    return (
        input_domain in INPUT_DOMAINS
        and output_domain in OUTPUT_DOMAINS
        and input_domain != output_domain
    )
