"""Downloads film genres from Wikidata, which links about 330 000 items to their ČSFD ID (property P2529).

ČSFD's own genres are only on each film's page (a request per film); Wikidata has them for free for about 240 000 of
those films. Its genres are fine-grained ("science fiction comedy", "space western"), so each is mapped to the broad
genres below through Wikidata's "subclass of" links. `python3 -m csfd.crawl genres` stores them in the film_genres
table (run-crawl.sh refreshes it weekly); the export includes them as genres.csv.gz.
"""

import csv
import io
import re
import urllib.parse
import urllib.request

SPARQL_URL = "https://query.wikidata.org/sparql"
ENTITY = "http://www.wikidata.org/entity/"

# Name used with --genre: Wikidata items whose subclasses count as that genre.
GENRES = {
    "scifi": ["Q24925"],
    "fantasy": ["Q132311"],
    "horor": ["Q200092"],
    "thriller": ["Q182015"],
    "krimi": ["Q959790"],
    "akcni": ["Q188473"],
    "dobrodruzny": ["Q319221"],
    "komedie": ["Q40831"],
    "drama": ["Q130232"],
    "romanticky": ["Q1054574"],
    "animovany": ["Q202866", "Q581714"],
    "anime": ["Q1107"],
    "dokumentarni": ["Q93204"],
    "valecny": ["Q369747"],
    "western": ["Q172980"],
    "historicky": ["Q17013749"],
    "zivotopisny": ["Q645928"],
    "muzikal": ["Q842256"],
    "rodinny": ["Q1361932"],
    "mysteriozni": ["Q1200678"],
}


def sparql(query):
    request = urllib.request.Request(SPARQL_URL, data=urllib.parse.urlencode({"query": query}).encode(),
                                     headers={"Accept": "text/csv", "User-Agent": "csfd-recommender/0.1"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return list(csv.DictReader(io.StringIO(response.read().decode("utf-8"))))


def qid(uri):
    value = uri[len(ENTITY):] if uri.startswith(ENTITY) else ""
    return value if re.fullmatch(r"Q\d+", value) else None


def download():
    """{ČSFD film ID: set of broad genre names}."""
    film_genres = {}
    for row in sparql("SELECT ?csfd ?g WHERE { ?item wdt:P2529 ?csfd ; wdt:P136 ?g . }"):
        genre = qid(row["g"])
        if genre and row["csfd"].isdigit():
            film_genres.setdefault(int(row["csfd"]), set()).add(genre)

    # The whole "subclass of" closure in one query times out, so walk it level by level.
    parents, todo = {}, {g for genres in film_genres.values() for g in genres}
    while todo:
        batch = sorted(todo)[:300]
        todo.difference_update(batch)
        for genre in batch:
            parents.setdefault(genre, set())
        values = " ".join("wd:" + g for g in batch)
        for row in sparql(f"SELECT ?g ?p WHERE {{ VALUES ?g {{ {values} }} ?g wdt:P279 ?p }}"):
            child, parent = qid(row["g"]), qid(row["p"])
            if child and parent:
                parents[child].add(parent)
                if parent not in parents:
                    todo.add(parent)

    ancestors = {}

    def ancestors_of(genre):
        if genre not in ancestors:
            ancestors[genre] = {genre}  # Guards against cycles in the subclass graph.
            ancestors[genre] = {genre}.union(*(ancestors_of(p) for p in parents.get(genre, ())))
        return ancestors[genre]

    roots = {root: name for name, items in GENRES.items() for root in items}
    return {film: {roots[a] for g in genres for a in ancestors_of(g) if a in roots}
            for film, genres in film_genres.items()}
