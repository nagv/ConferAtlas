# Screenshots

The main README links to the images listed below. They were captured from the running app with the built-in demo data, using headless Microsoft Edge (see the command at the end).

How to capture:
1. Run the app with the built-in demo data (`.venv\Scripts\streamlit.exe run app/main.py`).
2. Use a browser window of about 1400×900, with no filters applied unless noted.
3. Save each PNG here with the exact file name.

| File | Page and state |
|---|---|
| `overview.png` | Portfolio overview, with *Last calendar year* applied so the KPI cards show changes |
| `event-drilldown.png` | Event drill-down for one hybrid event, scrolled to the Sessions section |
| `sponsor-performance.png` | Sponsor performance, showing the leads-vs-target chart and the KPI cards |
| `audience.png` | Audience insights, showing the world map |
| `feedback.png` | Feedback analyzer, showing the rating distribution and sentiment by event |
| `ask.png` | Ask the portfolio, with one answer and *View calculations* open (needs an API key) |
| `mobile.png` | Portfolio overview in a phone-sized window (about 375 px wide) |

## Recapturing

With the app running at http://127.0.0.1:8501, run this from PowerShell. It saves to a temporary folder,
because Edge's `--screenshot` argument breaks on paths with spaces:

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$tmp = "$env:TEMP\conferatlas-shots"
& $edge --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$tmp\profile" `
  --window-size=1400,1100 --virtual-time-budget=40000 --screenshot="$tmp\overview.png" http://127.0.0.1:8501/overview
```

Window sizes used: 1400×1100 (overview), 1400×1700 (event drill-down), 1400×1500 (sponsor performance),
1400×2800 (audience, so the map is included), 1400×1250 (feedback), 1400×1000 (ask) and 480×1300 (mobile).
The Ask screenshot shows the starter questions; an answered question needs a live API call, so capture that one manually.
