"""
landmark_movies.py

Lists movies that will START playing at Landmark Closter Plaza (Closter, NJ)
on a future date. Movies already playing today (or recently) are left out.

Data source: the same JSON endpoint landmarktheatres.com's own theater page
uses. It returns, for each movie, the list of dates that have showtimes:

    GET https://www.landmarktheatres.com/api/gatsby-source-boxofficeapi/scheduledMovies?theaterId=G01AA
    -> {"scheduledDays": {"296642": ["2026-09-24", "2026-09-25", ...], ...}, ...}

Movie titles come from a second endpoint on the same site:

    GET .../movies?basic=true&ids=296642&ids=...
    -> [{"id": "296642", "title": "Forgotten Island", ...}, ...]

Usage:
    python landmark_movies.py              (uses today's date)
    python landmark_movies.py 2026-10-01   (pretend today is another date - for testing)
"""

import sys
import datetime
import requests

API_BASE = "https://www.landmarktheatres.com/api/gatsby-source-boxofficeapi"
THEATER_ID = "G01AA"  # Landmark Closter Plaza


def fetch_scheduled_days():
    """Return a dict of movie_id -> list of "YYYY-MM-DD" date strings."""
    url = API_BASE + "/scheduledMovies"
    response = requests.get(url, params={"theaterId": THEATER_ID}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data or "scheduledDays" not in data:
        raise ValueError("Unexpected response from scheduledMovies: %r" % str(data)[:200])
    return data["scheduledDays"]


def fetch_titles(movie_ids):
    """Return a dict of movie_id -> title. Missing titles are simply absent."""
    if not movie_ids:
        return {}
    url = API_BASE + "/movies"
    params = [("basic", "true")] + [("ids", movie_id) for movie_id in movie_ids]
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    titles = {}
    for movie in response.json() or []:
        # Landmark can override the title (e.g. for special screenings);
        # use that first, like their website does.
        exhibitor = movie.get("exhibitor") or {}
        title = exhibitor.get("title") or movie.get("title")
        if title:
            titles[str(movie["id"])] = title.strip()
    return titles


def parse_date(text):
    """Turn "2026-09-24" into a date. Returns None if it can't be parsed."""
    try:
        return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def find_premieres(scheduled_days, today):
    """
    Given movie_id -> list of date strings, return a list of
    (first_date, movie_id) for movies whose FIRST listed date is after today.
    Movies with any showtime today or earlier are skipped entirely.
    """
    premieres = []
    for movie_id, date_strings in scheduled_days.items():
        dates = [parse_date(d) for d in (date_strings or [])]
        dates = [d for d in dates if d is not None]
        if not dates:
            continue
        first_date = min(dates)
        if first_date > today:
            premieres.append((first_date, str(movie_id)))
    premieres.sort()
    return premieres


def last_listed_date(scheduled_days):
    """The furthest-out date that has any showtime, or None."""
    all_dates = []
    for date_strings in scheduled_days.values():
        for d in date_strings or []:
            parsed = parse_date(d)
            if parsed:
                all_dates.append(parsed)
    if not all_dates:
        return None
    return max(all_dates)


def pretty_date(d):
    """September 26, 2026 (no leading zero on the day)."""
    return "%s %d, %d" % (d.strftime("%B"), d.day, d.year)


def main():
    if len(sys.argv) > 1:
        today = parse_date(sys.argv[1])
        if today is None:
            print("Date must look like 2026-10-01")
            sys.exit(1)
    else:
        today = datetime.date.today()

    try:
        scheduled_days = fetch_scheduled_days()
        premieres = find_premieres(scheduled_days, today)
        titles = fetch_titles([movie_id for first_date, movie_id in premieres])
    except (requests.RequestException, ValueError) as error:
        print("Could not get showtimes from landmarktheatres.com:")
        print("  %s" % error)
        sys.exit(1)

    print("Upcoming Landmark Closter Premieres")
    print("-----------------------------------")
    print("(as of %s)" % pretty_date(today))
    print("")

    if not premieres:
        print("No upcoming premieres found.")
    else:
        current_date = None
        for first_date, movie_id in premieres:
            if first_date != current_date:
                if current_date is not None:
                    print("")
                print(pretty_date(first_date))
                current_date = first_date
            print(titles.get(movie_id, "Unknown title (movie id %s)" % movie_id))

    last_date = last_listed_date(scheduled_days)
    if last_date:
        print("")
        print("Note: Landmark has showtimes posted through %s." % pretty_date(last_date))
        print("Anything later has not been scheduled yet.")


if __name__ == "__main__":
    main()
