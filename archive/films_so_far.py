"""
ARCHIVED: "Films so far" table (removed from the viewer on 2026-10-10).

Not run by anything. To bring it back:
  1. Paste everything below (FILM_SUMMARY_LIMIT and build_film_summary) into
     recorder.py, just above build_page().
  2. In build_page(), after upcoming_table is built, add:
         film_summary = build_film_summary(showtimes, titles, runtimes, seats, now_local)
  3. In the page template, after the "Coming up" table, add:
         <h2>Films so far</h2>
         %s
     and add film_summary to the matching spot in the list of values after the template.

It uses these from recorder.py: datetime, nice_day, table, to_local,
AD_MINUTES, DEFAULT_RUNTIME.

What it showed: one row per film with a finished screening before today:
screenings, first/last date, days played, average % full, seats sold.
Most recently played first, up to 20 films. "+" marked films already
playing when recording began (Sep 24, 2026).
"""

FILM_SUMMARY_LIMIT = 20   # most recently played films first


def build_film_summary(showtimes, titles, runtimes, seats, now_local):
    """
    One row per film that has at least one finished screening before today:
    average % full, how long it has played (first to last screening), and
    seats sold. Today and the future are left out.

    A screening counts once it is on an earlier day and start + ads + runtime
    has passed (a late show can run past midnight). Its attendance is last_pct,
    the last reading the recorder saw (checked: for every show so far this
    equals the last reading before the show ended).
    """
    now_naive = now_local.replace(tzinfo=None)
    today = now_naive.date()

    # The day recording began. Films already playing then started earlier than we can see.
    first_seen = min(r["first_seen_utc"] for r in showtimes)
    recording_began = to_local(first_seen, now_local.tzinfo).date()

    films = {}
    for r in showtimes:
        if r["last_pct"] == "":
            continue
        start = datetime.datetime.strptime(r["starts_at"], "%Y-%m-%d %H:%M")
        runtime = runtimes.get(r["movie_id"], DEFAULT_RUNTIME)
        if start.date() >= today:
            continue  # today and the future are left out
        if start + datetime.timedelta(minutes=AD_MINUTES + runtime) > now_naive:
            continue  # last night's late show still running
        film = films.setdefault(r["movie_id"], {"shows": 0, "pct_total": 0, "seats_sold": 0,
                                                "unknown_screen": 0, "first": start.date(), "last": start.date()})
        pct = int(r["last_pct"])
        film["shows"] += 1
        film["pct_total"] += pct
        if r["screen_name"] in seats:
            film["seats_sold"] += round(pct * seats[r["screen_name"]] / 100)
        else:
            film["unknown_screen"] += 1
        film["first"] = min(film["first"], start.date())
        film["last"] = max(film["last"], start.date())

    # Most recently played first (ties: more seats sold first), then keep the top 20.
    recent_first = sorted(films.items(), key=lambda item: (item[1]["last"], item[1]["seats_sold"]), reverse=True)
    rows = []
    for movie_id, film in recent_first[:FILM_SUMMARY_LIMIT]:
        days = (film["last"] - film["first"]).days + 1
        more = "+" if film["first"] <= recording_began else ""
        dates = nice_day(film["first"].isoformat())
        if film["last"] != film["first"]:
            dates += " to " + nice_day(film["last"].isoformat())
        seats_text = "%d%s" % (film["seats_sold"], more)
        if film["unknown_screen"]:
            seats_text += " (%d shows in unknown screens)" % film["unknown_screen"]
        rows.append([titles.get(movie_id, movie_id), film["shows"], dates, "%d%s" % (days, more),
                     "%d%%" % round(film["pct_total"] / float(film["shows"])), seats_text])

    if not rows:
        return "<p>No finished screenings yet.</p>"
    return (table(["Movie", "Screenings", "Played", "Days", "Avg % full", "Seats sold"], rows) +
            "<p class='legend'>Screenings before today only. Most recently played first, up to %d films. "
            "Avg %% full = average of each screening's final %% sold. Days = first to last screening. "
            "+ = was already playing when recording began (%s), so the real run and total are larger.</p>"
            % (FILM_SUMMARY_LIMIT, nice_day(recording_began.isoformat())))
