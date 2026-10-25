# Closter Showtimes Recorder

I work at Landmark Closter Plaza, a small movie theater in Closter, NJ. Staffing and concession prep there are planned mostly on gut feel. This project records the theater's public showtime and ticket-sales data on a schedule so that, after a few months, demand can be measured instead of guessed.

**Viewer page:** https://kovar1.github.io/closter-showtimes/

## What it records

Every 15 minutes a GitHub Actions job runs `recorder.py`, which:

1. Pulls every posted showtime from Landmark's public showtime API, along with the percent of seats sold.
2. Logs a snapshot whenever a showtime's percent sold changes, so each show has a sales curve over time, not just a final number.
3. Saves details for each new film (runtime, rating, genre, studio, release date).
4. Once an hour, logs local weather from Open-Meteo.
5. Logs whether each run succeeded, so gaps in the data are visible rather than silent.
6. Rebuilds the viewer page in `docs/`.

The workflow then commits the new data and the rebuilt page back to the repo.

## Data

| File | One row per |
|---|---|
| `data/showtimes.csv` | showtime ever posted, including when it appeared and if it was pulled |
| `data/snapshots/YYYY-MM.csv` | change in a showtime's percent sold |
| `data/movies.csv` | film |
| `data/screens.csv` | auditorium, with seat counts taken from the booking seat map |
| `data/weather/YYYY-MM.csv` | hourly weather reading |
| `data/polls/YYYY-MM.csv` | recorder run (success or error) |

Times ending in `_utc` are UTC. `starts_at` is the theater's local time, exactly as the API gives it.

## Questions it's built to answer

- Which time slots and days actually sell, by film and genre?
- How far ahead do tickets sell, and how much is walk-up on the day?
- Does weather move attendance?
- Which showtimes consistently run near-empty and could be cut or moved?

These feed three practical uses: staffing forecasts, concession prep, and spotting weak showtimes.

## Status

Collecting data since September 2026. Analysis comes once there are a few months of history; the theater manager has approved presenting the results to Landmark corporate.

## Design choices

- **Plain Python, minimal dependencies** (`requests` only), so anyone at the theater could read and maintain it.
- **CSV in git instead of a database.** The data is small, every change is versioned, and it opens directly in Excel.
- **Runs on GitHub Actions**, so there's no server to keep alive.

## Run it locally

```bash
pip install -r requirements.txt
python recorder.py
```
