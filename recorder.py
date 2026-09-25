"""
recorder.py

Records Landmark Closter Plaza's public showtime data every time it runs.
GitHub Actions runs it every ~15 minutes (see .github/workflows/record.yml).

Each run:
  1. Fetches every posted showtime (today onward) and its percent of seats sold.
  2. Updates data/showtimes.csv (one row per showtime ever posted).
  3. Appends a row to data/snapshots/YYYY-MM.csv whenever a showtime's percent changes.
  4. Saves details for any movie it hasn't seen before to data/movies.csv.
  5. Once an hour, appends current weather to data/weather/YYYY-MM.csv.
  6. Appends one row to data/polls/YYYY-MM.csv saying whether this run worked.
  7. Rewrites docs/index.html, the viewer page.

All *_utc columns are UTC. starts_at is the theater's local time (Eastern),
exactly as Landmark's API gives it.

Run it by hand with:  python recorder.py
"""

import csv
import datetime
import html
import json
import os
import traceback
import zoneinfo

import requests

THEATER_ID = "G01AA"  # Landmark Closter Plaza
THEATER_TZ = "America/New_York"
API_BASE = "https://www.landmarktheatres.com/api/gatsby-source-boxofficeapi"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
LATITUDE = 40.9706
LONGITUDE = -73.9586
DAYS_AHEAD = 180
HEADERS = {"User-Agent": "ClosterRecorder/1.0 (personal project)"}

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
DOCS_DIR = os.path.join(HERE, "docs")

SHOWTIME_COLUMNS = ["showtime_key", "theater_id", "movie_id", "starts_at", "screen_name",
                    "tags", "first_seen_utc", "last_seen_utc", "removed_utc", "last_pct"]
MOVIE_COLUMNS = ["movie_id", "title", "runtime_minutes", "rating", "genres",
                 "release_date", "studio", "first_seen_utc"]
SNAPSHOT_COLUMNS = ["showtime_key", "polled_at_utc", "occupancy_pct"]
POLL_COLUMNS = ["polled_at_utc", "ok", "error", "showtimes_seen", "snapshots_written"]
WEATHER_COLUMNS = ["polled_at_utc", "observed_local", "temp_f", "precip_in",
                   "cloud_pct", "weather_code"]


# ---------------------------------------------------------------- CSV helpers

def read_csv(path):
    """Return a list of dicts, or [] if the file doesn't exist yet."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, columns, rows):
    """Replace the whole file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_path, path)  # so a crash never leaves a half-written file


def append_csv(path, columns, rows):
    """Add rows to the end of the file, writing the header if the file is new."""
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def monthly_path(folder, now_utc):
    return os.path.join(DATA_DIR, folder, now_utc.strftime("%Y-%m") + ".csv")


# ---------------------------------------------------------------- fetching

def fetch_showtimes(today_local):
    """Return a list of dicts, one per posted showtime from today onward."""
    theater = json.dumps({"id": THEATER_ID, "timeZone": THEATER_TZ}, separators=(",", ":"))
    last_day = today_local + datetime.timedelta(days=DAYS_AHEAD)
    params = {
        "from": today_local.isoformat() + "T00:00:00",
        "to": last_day.isoformat() + "T00:00:00",
        "theaters": theater,
    }
    response = requests.get(API_BASE + "/schedule", params=params, headers=HEADERS, timeout=60)
    response.raise_for_status()
    data = response.json()
    if not data or THEATER_ID not in data:
        raise ValueError("schedule response has no data for %s: %r" % (THEATER_ID, str(data)[:200]))

    showtimes = []
    for movie_id, days in data[THEATER_ID]["schedule"].items():
        for day, shows in days.items():
            for show in shows:
                starts_at = show["startsAt"][:16].replace("T", " ")  # '2026-09-24 19:00'
                screen_name = (show.get("screen") or {}).get("name") or "Unknown screen"
                occupancy = (show.get("occupancy") or {}).get("rate")
                showtimes.append({
                    "showtime_key": "|".join([THEATER_ID, str(movie_id), starts_at, screen_name]),
                    "movie_id": str(movie_id),
                    "starts_at": starts_at,
                    "screen_name": screen_name,
                    "tags": ",".join(show.get("tags") or []),
                    "pct": "" if occupancy is None else str(occupancy),
                })
    return showtimes


def fetch_movies(movie_ids):
    """Return a list of dicts in MOVIE_COLUMNS format (first_seen_utc left blank)."""
    params = [("basic", "false"), ("castingLimit", "0")] + [("ids", m) for m in movie_ids]
    response = requests.get(API_BASE + "/movies", params=params, headers=HEADERS, timeout=60)
    response.raise_for_status()

    movies = []
    for movie in response.json() or []:
        exhibitor = movie.get("exhibitor") or {}
        runtime_seconds = movie.get("runtime")
        runtime_minutes = ""
        if runtime_seconds and str(runtime_seconds).isdigit():
            runtime_minutes = str(round(int(runtime_seconds) / 60))
        movies.append({
            "movie_id": str(movie.get("id")),
            "title": exhibitor.get("title") or movie.get("title") or "",
            "runtime_minutes": runtime_minutes,
            "rating": movie.get("certificate") or "",
            "genres": movie.get("genres") or "",
            "release_date": (movie.get("release") or "")[:10],
            "studio": (movie.get("studio") or {}).get("name") or "",
            "first_seen_utc": "",
        })
    return movies


def fetch_weather():
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "current": "temperature_2m,precipitation,cloud_cover,weather_code",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": THEATER_TZ,
    }
    response = requests.get(WEATHER_URL, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    current = response.json()["current"]
    return {
        "observed_local": current.get("time", "").replace("T", " "),
        "temp_f": current.get("temperature_2m"),
        "precip_in": current.get("precipitation"),
        "cloud_pct": current.get("cloud_cover"),
        "weather_code": current.get("weather_code"),
    }


# ---------------------------------------------------------------- recording

def record_showtimes(now_utc, now_local):
    """Fetch showtimes and update showtimes.csv + snapshots. Returns (seen, written)."""
    stamp = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    showtimes_path = os.path.join(DATA_DIR, "showtimes.csv")

    known = {}
    for row in read_csv(showtimes_path):
        known[row["showtime_key"]] = row

    fetched = fetch_showtimes(now_local.date())

    new_snapshots = []
    seen_keys = set()
    for show in fetched:
        key = show["showtime_key"]
        if key in seen_keys:
            continue  # the API occasionally lists the same showtime twice
        seen_keys.add(key)

        row = known.get(key)
        if row is None:
            row = {
                "showtime_key": key,
                "theater_id": THEATER_ID,
                "movie_id": show["movie_id"],
                "starts_at": show["starts_at"],
                "screen_name": show["screen_name"],
                "tags": show["tags"],
                "first_seen_utc": stamp,
                "last_seen_utc": stamp,
                "removed_utc": "",
                "last_pct": "",
            }
            known[key] = row

        row["last_seen_utc"] = stamp
        row["removed_utc"] = ""  # if it came back, it isn't removed
        row["tags"] = show["tags"]

        if show["pct"] != "" and show["pct"] != row["last_pct"]:
            new_snapshots.append({"showtime_key": key, "polled_at_utc": stamp,
                                  "occupancy_pct": show["pct"]})
            row["last_pct"] = show["pct"]

    # A future showtime we knew about that is no longer listed = cancelled or moved.
    now_local_text = now_local.strftime("%Y-%m-%d %H:%M")
    for key, row in known.items():
        if key in seen_keys or row["removed_utc"]:
            continue
        if row["starts_at"] > now_local_text:
            row["removed_utc"] = stamp

    rows = sorted(known.values(), key=lambda r: (r["starts_at"], r["screen_name"]))
    write_csv(showtimes_path, SHOWTIME_COLUMNS, rows)
    append_csv(monthly_path("snapshots", now_utc), SNAPSHOT_COLUMNS, new_snapshots)
    return len(seen_keys), len(new_snapshots)


def record_new_movies(now_utc):
    """Look up details for any movie_id in showtimes.csv that isn't in movies.csv yet."""
    movies_path = os.path.join(DATA_DIR, "movies.csv")
    known_ids = set(row["movie_id"] for row in read_csv(movies_path))
    wanted = sorted(set(row["movie_id"] for row in read_csv(os.path.join(DATA_DIR, "showtimes.csv"))) - known_ids)
    if not wanted:
        return
    movies = fetch_movies(wanted)
    for movie in movies:
        movie["first_seen_utc"] = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    append_csv(movies_path, MOVIE_COLUMNS, movies)


def record_weather_if_due(now_utc):
    """Save weather at most once per UTC hour."""
    path = monthly_path("weather", now_utc)
    rows = read_csv(path)
    this_hour = now_utc.strftime("%Y-%m-%d %H")
    if rows and rows[-1]["polled_at_utc"][:13] == this_hour:
        return
    weather = fetch_weather()
    weather["polled_at_utc"] = now_utc.strftime("%Y-%m-%d %H:%M:%S")
    append_csv(path, WEATHER_COLUMNS, [weather])


# ---------------------------------------------------------------- viewer page

def to_local(utc_text, tz):
    """'2026-09-24 21:30:05' (UTC) -> datetime in the theater's time zone."""
    parsed = datetime.datetime.strptime(utc_text, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=datetime.timezone.utc).astimezone(tz)


def nice_time(starts_at):
    """'2026-09-24 19:05' -> '7:05 PM'"""
    parsed = datetime.datetime.strptime(starts_at, "%Y-%m-%d %H:%M")
    return parsed.strftime("%I:%M %p").lstrip("0")


def nice_day(day_text):
    """'2026-09-24' -> 'Thu Sep 24'"""
    parsed = datetime.datetime.strptime(day_text, "%Y-%m-%d")
    return parsed.strftime("%a %b ") + str(parsed.day)


def table(headers, rows):
    """Plain HTML table. rows is a list of lists; everything is escaped."""
    out = ["<table><tr>"]
    out += ["<th>%s</th>" % html.escape(h) for h in headers]
    out.append("</tr>")
    for row in rows:
        out.append("<tr>" + "".join("<td>%s</td>" % html.escape(str(c)) for c in row) + "</tr>")
    out.append("</table>")
    return "\n".join(out)


AD_MINUTES = 10           # trailers/ads before every feature
PIXELS_PER_MINUTE = 1.5
DEFAULT_RUNTIME = 120     # used when a movie's runtime is unknown
HEADER_PX = 34            # room for screen names above the chart


def build_timeline(rows, titles, runtimes, seats, now_local):
    """
    One column per screen. Each show is a box: top = start time,
    height = ads + runtime. The shaded part of the box grows left to right
    with the percent of seats sold.
    """
    if not rows:
        return "<p>No shows today.</p>"

    screens = [name for name in seats]
    for r in rows:
        if r["screen_name"] not in screens:
            screens.append(r["screen_name"])

    # Work out each show's end time first.
    shows = []
    for r in rows:
        runtime = runtimes.get(r["movie_id"])
        guessed = runtime is None
        if guessed:
            runtime = DEFAULT_RUNTIME
        start = datetime.datetime.strptime(r["starts_at"], "%Y-%m-%d %H:%M")
        end = start + datetime.timedelta(minutes=AD_MINUTES + runtime)
        shows.append({"row": r, "start": start, "end": end, "runtime": runtime, "guessed": guessed})

    # The chart covers whole hours from the first start to the last end.
    first = min(s["start"] for s in shows).replace(minute=0)
    last = max(s["end"] for s in shows)
    if last.minute:
        last = last.replace(minute=0) + datetime.timedelta(hours=1)
    total_minutes = (last - first).total_seconds() / 60
    height = total_minutes * PIXELS_PER_MINUTE
    column_count = len(screens)

    def column_style(index):
        return ("left: calc(44px + (100%% - 44px) * %d / %d); width: calc((100%% - 44px) / %d - 4px);"
                % (index, column_count, column_count))

    out = ['<div class="timeline" style="height: %dpx;">' % (height + HEADER_PX)]

    # Screen names across the top.
    for i, name in enumerate(screens):
        label = html.escape(name.replace("Screen ", "#"))
        if name in seats:
            label += "<br><small>%d seats</small>" % seats[name]
        out.append('<div class="colhead" style="%s">%s</div>' % (column_style(i), label))

    # Hour lines.
    hour = first
    while hour <= last:
        top = HEADER_PX + (hour - first).total_seconds() / 60 * PIXELS_PER_MINUTE
        out.append('<div class="hourline" style="top: %dpx;"><span>%s</span></div>'
                   % (top, hour.strftime("%I %p").lstrip("0")))
        hour += datetime.timedelta(hours=1)

    # Show boxes, plus the gap to the next show on the same screen.
    for i, name in enumerate(screens):
        column_shows = sorted([s for s in shows if s["row"]["screen_name"] == name], key=lambda s: s["start"])
        for n, s in enumerate(column_shows):
            r = s["row"]
            top = HEADER_PX + (s["start"] - first).total_seconds() / 60 * PIXELS_PER_MINUTE
            box_height = (s["end"] - s["start"]).total_seconds() / 60 * PIXELS_PER_MINUTE
            pct = int(r["last_pct"]) if r["last_pct"] not in ("", None) else 0
            pct = max(0, min(pct, 100))
            capacity = seats.get(name)
            if capacity:
                count = "%d/%d" % (round(pct * capacity / 100), capacity)
            else:
                count = "%d%%" % pct

            classes = ["show"]
            if pct == 0:
                classes.append("empty")
            if pct >= 100:
                classes.append("soldout")
            if s["end"] <= now_local.replace(tzinfo=None):
                classes.append("done")

            status = "SOLD OUT" if pct >= 100 else count
            ends = s["end"].strftime("%I:%M").lstrip("0")
            if s["guessed"]:
                ends += "?"
            out.append(
                '<div class="%s" style="%s top: %dpx; height: %dpx;" title="%s">'
                '<div class="ads" style="height: %dpx;"></div>'
                '<div class="fill" style="top: %dpx; width: %d%%;"></div>'
                '<div class="label"><b>%s</b><div class="title">%s</div><span class="count">%s</span><br><small>ends %s</small></div>'
                '</div>'
                % (" ".join(classes), column_style(i), top, box_height,
                   html.escape("%s, %s, %d%% sold" % (titles.get(r["movie_id"], ""), nice_time(r["starts_at"]), pct)),
                   AD_MINUTES * PIXELS_PER_MINUTE,
                   AD_MINUTES * PIXELS_PER_MINUTE, pct,
                   nice_time(r["starts_at"]), html.escape(titles.get(r["movie_id"], r["movie_id"])),
                   status, ends))

            # Gap until the next show in this screen (cleaning/changeover time).
            if n + 1 < len(column_shows):
                following = column_shows[n + 1]
                gap = (following["start"] - s["end"]).total_seconds() / 60
                gap_top = top + box_height
                gap_class = "gap tight" if gap < 15 else "gap"
                out.append('<div class="%s" style="%s top: %dpx;">%s</div>'
                           % (gap_class, column_style(i), gap_top,
                              ("%d min gap" % gap) if gap >= 0 else ("overlaps %d min" % -gap)))

    # "Now" line, if now is inside the chart.
    now_naive = now_local.replace(tzinfo=None, second=0, microsecond=0)
    if first <= now_naive <= last:
        top = HEADER_PX + (now_naive - first).total_seconds() / 60 * PIXELS_PER_MINUTE
        out.append('<div class="nowline" style="top: %dpx;"><span>%s</span></div>'
                   % (top, now_naive.strftime("%I:%M").lstrip("0")))

    out.append("</div>")
    return "\n".join(out)


def build_page(now_utc, tz):
    now_local = now_utc.astimezone(tz)
    today = now_local.strftime("%Y-%m-%d")
    week_end = (now_local + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    showtimes = [r for r in read_csv(os.path.join(DATA_DIR, "showtimes.csv")) if not r["removed_utc"]]
    movies = read_csv(os.path.join(DATA_DIR, "movies.csv"))
    titles = {r["movie_id"]: r["title"] for r in movies}
    runtimes = {r["movie_id"]: int(r["runtime_minutes"]) for r in movies if r["runtime_minutes"].isdigit()}
    seats ={r["screen_name"]: int(r["seats"]) for r in read_csv(os.path.join(DATA_DIR, "screens.csv"))}

    def people(row):
        if row["last_pct"] == "" or row["screen_name"] not in seats:
            return ""
        return round(int(row["last_pct"]) * seats[row["screen_name"]] / 100)

    # 1. Health: this month's and last month's poll logs, last 24 hours.
    last_month_utc = now_utc.replace(day=1) - datetime.timedelta(days=1)
    polls = read_csv(monthly_path("polls", last_month_utc)) + read_csv(monthly_path("polls", now_utc))
    day_ago = now_utc - datetime.timedelta(hours=24)
    recent = [p for p in polls if to_local(p["polled_at_utc"], datetime.timezone.utc) >= day_ago]
    ok_runs = [p for p in recent if p["ok"] == "1"]
    failed_runs = [p for p in recent if p["ok"] != "1"]
    gaps = []
    for earlier, later in zip(recent, recent[1:]):
        a = to_local(earlier["polled_at_utc"], tz)
        b = to_local(later["polled_at_utc"], tz)
        if (b - a).total_seconds() > 45 * 60:
            gaps.append("%s to %s" % (a.strftime("%a %I:%M %p"), b.strftime("%a %I:%M %p")))

    health = ["<p>Page built %s.</p>" % html.escape(now_local.strftime("%a %b %d, %I:%M %p %Z"))]
    health.append("<p>Last 24 hours: %d successful runs, %d failed.</p>" % (len(ok_runs), len(failed_runs)))
    if failed_runs:
        health.append("<p class='bad'>Latest error: %s</p>" % html.escape(failed_runs[-1]["error"][:300]))
    if gaps:
        health.append("<p class='bad'>Gaps over 45 minutes: %s</p>" % html.escape("; ".join(gaps)))

    # 2. Today
    today_rows = sorted([r for r in showtimes if r["starts_at"][:10] == today], key=lambda r: r["starts_at"])
    today_table = table(
        ["Time", "Screen", "Movie", "% sold", "~People"],
        [[nice_time(r["starts_at"]), r["screen_name"].replace("Screen ", ""),
          titles.get(r["movie_id"], r["movie_id"]), r["last_pct"], people(r)] for r in today_rows])
    today_total = sum(people(r) or 0 for r in today_rows)

    # 3. Next 7 days, only shows with sales, best-selling first
    week_rows = [r for r in showtimes if today < r["starts_at"][:10] <= week_end
                 and r["last_pct"] not in ("", "0")]
    week_rows.sort(key=lambda r: -int(r["last_pct"]))
    week_table = table(
        ["Day", "Time", "Screen", "Movie", "% sold", "~People"],
        [[nice_day(r["starts_at"][:10]), nice_time(r["starts_at"]), r["screen_name"].replace("Screen ", ""),
          titles.get(r["movie_id"], r["movie_id"]), r["last_pct"], people(r)] for r in week_rows])

    # 4. Coming up: movies whose first recorded showtime is after today
    first_day = {}
    for r in showtimes:
        day = r["starts_at"][:10]
        if r["movie_id"] not in first_day or day < first_day[r["movie_id"]]:
            first_day[r["movie_id"]] = day
    upcoming = sorted((day, titles.get(mid, mid)) for mid, day in first_day.items() if day > today)
    upcoming_table = table(["First show", "Movie"], [[nice_day(day), title] for day, title in upcoming])

    timeline = build_timeline(today_rows, titles, runtimes, seats, now_local)

    page = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Closter Recorder</title>
<style>
body { font-family: sans-serif; margin: 16px; }
table { border-collapse: collapse; margin-bottom: 8px; }
th, td { border: 1px solid #ccc; padding: 4px 8px; text-align: left; }
th { background: #eee; }
.bad { color: #b00; font-weight: bold; }

/* Two columns on a wide screen, stacked on a phone. */
.layout { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.left { flex: 1 1 300px; max-width: 760px; min-width: 0; }
.right { flex: 1 1 300px; min-width: 0; overflow-x: auto; }

/* Timeline */
.timeline { position: relative; font-size: 12px; }
.colhead { position: absolute; top: 0; height: 30px; text-align: center; font-weight: bold; line-height: 1.1; }
.colhead small { font-weight: normal; color: #666; }
.hourline { position: absolute; left: 0; right: 0; border-top: 1px solid #e4e4e4; }
.hourline span { position: absolute; top: -8px; left: 0; color: #888; font-size: 11px; background: #fff; }
.show { position: absolute; box-sizing: border-box; border: 1px solid #333; background: #fff; overflow: hidden; }
.show .ads { position: absolute; top: 0; left: 0; right: 0;
             background: repeating-linear-gradient(45deg, #d6d6d6 0 3px, #fff 3px 6px); border-bottom: 1px solid #999; }
.show .fill { position: absolute; left: 0; bottom: 0; background: #8fa8e0; }
.show .label { position: relative; padding: 16px 3px 2px 3px; line-height: 1.25; font-size: 11px; }
.show .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.show .count { font-size: 14px; font-weight: bold; }
.show.empty { border: 1px dashed #888; }
.show.soldout { border: 3px solid #1a2f6b; }
.show.soldout .fill { background: #5b7bd0; }
.show.soldout .count { color: #1a2f6b; letter-spacing: 1px; }
.show.done { opacity: 0.45; }
.gap { position: absolute; text-align: center; color: #666; font-size: 10px; }
.gap.tight { color: #b00; font-weight: bold; }
.nowline { position: absolute; left: 40px; right: 0; border-top: 2px solid #000; z-index: 5; }
.nowline span { position: absolute; top: -9px; left: -40px; background: #000; color: #fff; font-size: 11px; padding: 0 3px; }
.legend { font-size: 12px; color: #444; margin: 4px 0 10px 0; }
.swatch { display: inline-block; width: 28px; height: 12px; border: 1px solid #333; vertical-align: middle; }
</style></head><body>
<h1>Landmark Closter Plaza</h1>
<div class="layout">
<div class="left">
<h2>Today by screen (%s)</h2>
<div class="legend">
<span class="swatch" style="background: repeating-linear-gradient(45deg, #d6d6d6 0 3px, #fff 3px 6px);"></span> %d min ads &nbsp;
<span class="swatch" style="background: linear-gradient(90deg, #8fa8e0 40%%, #fff 40%%);"></span> shaded width = seats sold &nbsp;
<span class="swatch" style="border: 1px dashed #888;"></span> nothing sold &nbsp;
<span class="swatch" style="border: 3px solid #1a2f6b; background: #5b7bd0;"></span> sold out<br>
Box height = ads + runtime. Red gap = under 15 minutes to turn the room over.
</div>
%s
</div>
<div class="right">
<h2>Recorder health</h2>
%s
<h2>Today: about %d tickets sold across all shows</h2>
%s
<h2>Next 7 days: shows with sales</h2>
%s
<h2>Coming up: first showtimes of new movies</h2>
%s
<p>~People = %% sold x seats in that screen. Data: landmarktheatres.com, recorded every ~15 minutes.</p>
</div>
</div>
</body></html>
""" % (html.escape(nice_day(today)), AD_MINUTES, timeline,
       "\n".join(health), today_total, today_table, week_table, upcoming_table)

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


# ---------------------------------------------------------------- main

def main():
    tz = zoneinfo.ZoneInfo(THEATER_TZ)
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    now_local = now_utc.astimezone(tz)

    poll = {"polled_at_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"), "ok": "1", "error": "",
            "showtimes_seen": "", "snapshots_written": ""}
    try:
        seen, written = record_showtimes(now_utc, now_local)
        poll["showtimes_seen"] = seen
        poll["snapshots_written"] = written
        print("Showtimes seen: %d, sales changes recorded: %d" % (seen, written))
    except Exception as error:
        poll["ok"] = "0"
        poll["error"] = "showtimes: %s" % error
        traceback.print_exc()

    # Movies and weather are extras: if they fail, note it but keep going.
    for name, step in [("movies", lambda: record_new_movies(now_utc)),
                       ("weather", lambda: record_weather_if_due(now_utc))]:
        try:
            step()
        except Exception as error:
            poll["error"] = (poll["error"] + " | " if poll["error"] else "") + "%s: %s" % (name, error)
            traceback.print_exc()

    append_csv(monthly_path("polls", now_utc), POLL_COLUMNS, [poll])
    build_page(now_utc, tz)
    print("Done. ok=%s %s" % (poll["ok"], poll["error"]))


if __name__ == "__main__":
    main()
