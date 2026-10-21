"""Parsing of ČSFD user rating pages (https://www.csfd.cz/uzivatel/<id>/hodnoceni/)."""

import html
import re
from dataclasses import dataclass
from datetime import date

ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
FILM_RE = re.compile(r'<a href="/film/(?:(\d+)-[^/"]*/)?(\d+)-([^/"]*)/[^"]*" class="film-title-name">(.*?)</a>', re.S)
INFO_RE = re.compile(r'<span class="info">(.*?)</span>', re.S)
STARS_RE = re.compile(r'<span class="stars ([^"]*)">')
DATE_RE = re.compile(r'<td class="date-only[^"]*">\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\b')
PAGE_RE = re.compile(r'hodnoceni/\?page=(\d+)"')
CANONICAL_RE = re.compile(r'<link rel="canonical" href="https://www\.csfd\.cz/uzivatel/(\d+)-([^/"]*)/')
NAME_RE = re.compile(r"<title>\s*(.*?) \| Hodnocení \| ČSFD", re.S)


@dataclass
class Rating:
    film_id: int
    # For an episode or a season, the series it belongs to.
    parent_id: int | None
    slug: str
    title: str
    year: int | None
    kind: str
    # 0 = "odpad" (trash), 1-5 stars.
    stars: int
    rated_on: date | None


@dataclass
class RatingsPage:
    user_id: int | None
    user_slug: str | None
    user_name: str | None
    page_count: int
    ratings: list[Rating]


class NotRatingsPage(Exception):
    """The HTML is not a ratings page (anti-bot challenge, error page, changed layout...)."""


FAN_RE = re.compile(r'<a href="/uzivatel/(\d+)-[^/"]*/')
PROFILE_RE = re.compile(r'<link rel="canonical" href="https://www\.csfd\.cz/uzivatel/\d+-[^/"]*/prehled/')


def parse_fanclub(page_html):
    """User IDs in the "Fanklub" box of a profile (https://www.csfd.cz/uzivatel/<id>/prehled/): the users who added
    this one to their favourites. ČSFD doesn't show whom the user added."""
    if not PROFILE_RE.search(page_html):
        raise NotRatingsPage("not a profile page")
    start = page_html.find('id="snippet--fanclub"')
    if start < 0:
        return []
    box = page_html[start:page_html.find("</section>", start)]
    return list(dict.fromkeys(int(user_id) for user_id in FAN_RE.findall(box)))


def is_not_found(page_html):
    return "404 - " in page_html[:5000] and "Stránka nenalezena" in page_html[:5000]


def parse_ratings_page(page_html):
    start = page_html.find('id="snippet--ratings"')
    if start < 0:
        raise NotRatingsPage("no #snippet--ratings")
    end = page_html.find("</table>", start)
    table = page_html[start:end if end >= 0 else len(page_html)]

    ratings = [parse_row(row) for row in ROW_RE.findall(table)]

    canonical = CANONICAL_RE.search(page_html)
    name = NAME_RE.search(page_html)
    pages = [int(p) for p in PAGE_RE.findall(page_html)]
    return RatingsPage(
        user_id=int(canonical.group(1)) if canonical else None,
        user_slug=canonical.group(2) if canonical else None,
        user_name=html.unescape(name.group(1).strip()) if name else None,
        page_count=max(pages + [1]),
        ratings=ratings,
    )


def parse_row(row):
    film = FILM_RE.search(row)
    if not film:
        raise NotRatingsPage("row without a film link")
    parent_id, film_id, slug, title = film.groups()

    infos = [html.unescape(i.strip()) for i in INFO_RE.findall(row)]
    year = int(infos[0]) if infos and infos[0].isdigit() else None
    kinds = [i for i in infos if not i.isdigit()]

    stars_match = STARS_RE.search(row)
    if not stars_match:
        raise NotRatingsPage("row without stars")
    stars_class = stars_match.group(1).split()[-1]
    stars = int(stars_class.split("-")[1]) if stars_class.startswith("stars-") else 0

    date_match = DATE_RE.search(row)
    rated_on = None
    if date_match:
        day, month, year_rated = (int(g) for g in date_match.groups())
        rated_on = date(year_rated, month, day)

    return Rating(
        film_id=int(film_id),
        parent_id=int(parent_id) if parent_id else None,
        slug=slug,
        title=html.unescape(title.strip()),
        year=year,
        kind=kinds[0] if kinds else "film",
        stars=stars,
        rated_on=rated_on,
    )
