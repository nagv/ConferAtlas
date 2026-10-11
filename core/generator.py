"""Generate realistic synthetic event data for ConferAtlas.

All randomness flows from one seeded ``numpy.random.Generator`` and tables are
built in a fixed order, so the same seed always produces identical CSVs.

Built-in patterns:
- registrations spike just before each event's early-bird deadline, with a
  second rush in the final two weeks
- 15-30% of confirmed registrants are no-shows (virtual events run highest)
- session quality varies, a few sessions flop, and sessions.rating is the mean
  of that session's feedback ratings
- sponsors' actual leads vary around their target, so roughly a third miss it
- feedback comments follow the rating: praise, mixed or complaints
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from core.schema import EVENT_TYPES, FORMATS, TIERS

DEFAULT_SEED = 42
CITIES = ("Austin", "Boston", "Chicago", "New York", "San Francisco", "Toronto")
EARLY_BIRD_DAYS = 45  # early-bird pricing ends this many days before the event
REG_OPEN_DAYS = 120
REPEAT_RATE = 0.30  # share of registrants at a later event who attended an earlier one of the same type
SESSION_MINUTES = 45

_THEMES = {
    "tech": ("AI & Data Summit", "Cloud Native Conference", "DevSecOps Forum"),
    "business": ("Growth Leaders Summit", "Future of Work Forum", "Revenue Ops Conference"),
    "medical": ("Clinical Innovation Congress", "Digital Health Summit", "Oncology Research Forum"),
    "education": ("EdTech Leaders Summit", "Teaching & Learning Forum", "Higher Ed Futures Conference"),
}
_TRACKS = {
    "tech": ("AI & ML", "Cloud", "Security", "DevOps", "Product"),
    "business": ("Leadership", "Sales", "Marketing", "Finance", "Operations"),
    "medical": ("Clinical Practice", "Research", "Digital Health", "Policy", "Patient Care"),
    "education": ("Classroom Tech", "Assessment", "Leadership", "Inclusion", "Higher Ed"),
}
_ROLES = {
    "tech": ("Software Engineer", "Data Scientist", "Product Manager", "IT Director", "CTO", "Founder"),
    "business": ("Marketing Manager", "Sales Director", "Consultant", "Operations Manager", "CFO", "Founder"),
    "medical": ("Physician", "Nurse", "Researcher", "Hospital Administrator", "Pharmacist", "Medical Director"),
    "education": ("Teacher", "Professor", "Principal", "EdTech Specialist", "Administrator", "Dean"),
}
_ATTENDEE_INDUSTRIES = {
    "tech": ("Software", "Financial Services", "Retail", "Manufacturing", "Healthcare"),
    "business": ("Professional Services", "Financial Services", "Retail", "Software", "Manufacturing"),
    "medical": ("Hospitals", "Pharmaceuticals", "Medical Devices", "Health Insurance", "Academia"),
    "education": ("K-12", "Higher Education", "EdTech", "Government", "Non-profit"),
}
_SENIORITY = ("Entry", "Mid", "Senior", "Director", "VP", "C-level")
_SENIORITY_P = (0.15, 0.30, 0.27, 0.15, 0.08, 0.05)
_COUNTRIES = ("United States", "Canada", "United Kingdom", "Germany", "India", "Mexico", "Australia")

# Sponsor industry -> the event types it gravitates towards.
_SPONSOR_INDUSTRIES = {
    "Software": ("tech", "business"),
    "Cybersecurity": ("tech",),
    "Cloud Infrastructure": ("tech",),
    "Financial Services": ("business",),
    "Consulting": ("business",),
    "Pharmaceuticals": ("medical",),
    "Medical Devices": ("medical",),
    "Health IT": ("medical", "tech"),
    "EdTech": ("education", "tech"),
    "Publishing": ("education",),
}
_NAME_SUFFIX = {
    "Software": "Software", "Cybersecurity": "Security", "Cloud Infrastructure": "Cloud",
    "Financial Services": "Capital", "Consulting": "Partners", "Pharmaceuticals": "Therapeutics",
    "Medical Devices": "Medical", "Health IT": "Health", "EdTech": "Learning", "Publishing": "Press",
}
_NAME_PREFIX = (
    "Apex", "Bluefin", "Cedarline", "Lumen", "Vertexa", "Harborview", "Quillon", "Nimbus", "Orchidia",
    "Summitry", "Ironwood", "Kestrel", "Meridian", "Polarion", "Sablewood", "Tidewater", "Juniper",
    "Corvid", "Halcyon", "Brightpath", "Copperleaf", "Driftwood", "Evergrove", "Foxglove", "Granite",
    "Hollis", "Indigo", "Larkspur", "Mosaic", "Northgate",
)
_TIER_P = (0.15, 0.30, 0.35, 0.20)
_CONTRACT_RANGE = {"platinum": (120_000, 250_000), "gold": (60_000, 120_000),
                   "silver": (25_000, 60_000), "bronze": (8_000, 25_000)}
_GOALS = ("Lead generation", "Brand awareness", "Product launch", "Talent recruiting",
          "Partner development", "Customer retention")
_PACKAGE = {"platinum": "Title Sponsor", "gold": "Premier Booth",
            "silver": "Standard Booth", "bronze": "Exhibitor Table"}
_VIRTUAL_PACKAGE = {"platinum": "Digital Premier", "gold": "Digital Premier",
                    "silver": "Virtual Booth", "bronze": "Virtual Booth"}
_PACKAGE_LEADS = {"Title Sponsor": 220, "Premier Booth": 140, "Standard Booth": 80,
                  "Exhibitor Table": 40, "Digital Premier": 160, "Virtual Booth": 70}
_LEAD_SOURCES = {"in-person": (("booth", "demo"), (0.65, 0.35)),
                 "virtual": (("virtual",), (1.0,)),
                 "hybrid": (("booth", "demo", "virtual"), (0.45, 0.25, 0.30))}
_LEAD_NOTES = (
    "Interested in a demo for their team", "Asked for pricing on an enterprise plan",
    "Decision maker; wants a follow-up call next week", "Requested case studies",
    "Follow up after their budget cycle", "Currently using a competitor; contract ends soon",
    "Wants an intro to a solutions engineer", "Just browsing; low priority",
    "Not a fit right now", "Student; collected swag only", "Asked about integrations",
    "Strong interest; requested a trial account",
)
_FIRST = ("Ava", "Ben", "Chloe", "Diego", "Elena", "Farah", "Grace", "Hiro", "Isla", "Jamal", "Kira",
          "Leo", "Maya", "Nikhil", "Olivia", "Priya", "Quinn", "Rafael", "Sofia", "Tariq", "Uma",
          "Victor", "Wen", "Yara", "Zane")
_LAST = ("Adams", "Brooks", "Chen", "Duarte", "Evans", "Fischer", "Garcia", "Hughes", "Ibrahim",
         "Jensen", "Kowalski", "Lee", "Martin", "Nakamura", "Okafor", "Patel", "Reyes", "Silva",
         "Thompson", "Usman", "Varga", "Walsh", "Young", "Zhang")
_COMMENTS = {
    "positive": (
        "Excellent speaker, very practical takeaways.", "Best session of the day.",
        "Great examples I can use right away.", "Engaging and well paced.",
        "Loved the live demo.", "Clear, insightful and well prepared.",
        "Would attend again; the Q&A was great.", "Really useful frameworks and slides.",
    ),
    "neutral": (
        "Good content but ran a bit long.", "Decent overview, wanted more depth.",
        "Some useful points; slides were hard to read.", "OK session, a little basic for me.",
        "Interesting topic, delivery could be stronger.",
    ),
    "negative": (
        "Felt like a sales pitch.", "Audio problems made it hard to follow.",
        "Too basic and not what the abstract promised.", "Room was overcrowded and too hot.",
        "Speaker rushed through the slides.", "Stream kept buffering; gave up halfway.",
        "Started late and ended early, little substance.",
    ),
}


def _events(rng: np.random.Generator) -> pd.DataFrame:
    # Every type x format combination exactly once: 4 x 3 = 12 events.
    plan = [(t, f) for t in EVENT_TYPES for f in FORMATS]
    order = rng.permutation(len(plan))
    cities = rng.permutation(np.repeat(CITIES, 2))
    first, last = date(2024, 2, 1), date(2026, 9, 15)
    offsets = np.sort(rng.choice((last - first).days, size=len(plan), replace=False))

    rows = []
    for i, (plan_idx, city, offset) in enumerate(zip(order, cities, offsets), start=1):
        event_type, fmt = plan[plan_idx]
        start = first + timedelta(days=int(offset))
        days = int(rng.integers(1, 3)) if fmt == "virtual" else int(rng.integers(2, 4))
        base = {"tech": 1400, "business": 900, "medical": 1100, "education": 700}[event_type]
        size = int(base * {"in-person": 1.0, "hybrid": 1.1, "virtual": 1.3}[fmt] * rng.uniform(0.6, 1.3))
        cost_per_head = {"in-person": 320, "hybrid": 260, "virtual": 90}[fmt]
        budget = round(size * cost_per_head * rng.uniform(0.85, 1.15), -3)
        rows.append({
            "event_id": f"EV{i:03d}",
            "name": f"{city} {rng.choice(_THEMES[event_type])} {start.year}",
            "event_type": event_type,
            "format": fmt,
            "city": city,
            "start_date": start,
            "end_date": start + timedelta(days=days - 1),
            "budget": budget,
            "actual_cost": round(budget * rng.normal(1.0, 0.08), -2),
            "planned_size": size,
        })
    return pd.DataFrame(rows)


def _registration_days_before(rng: np.random.Generator, n: int) -> np.ndarray:
    """Days before the event each person registered, with an early-bird spike."""
    component = rng.choice(4, size=n, p=(0.12, 0.33, 0.25, 0.30))
    trickle = rng.uniform(EARLY_BIRD_DAYS + 10, REG_OPEN_DAYS, n)
    early_bird_rush = EARLY_BIRD_DAYS + rng.exponential(3, n)
    steady = rng.uniform(15, EARLY_BIRD_DAYS, n)
    last_minute = np.minimum(rng.exponential(5, n), 14)
    days = np.choose(component, (trickle, early_bird_rush, steady, last_minute))
    return np.clip(days, 0, REG_OPEN_DAYS).astype(int)


def _registrations(rng: np.random.Generator, events: pd.DataFrame) -> pd.DataFrame:
    frames, next_id = [], 1
    for ev in events.itertuples():
        n = ev.planned_size
        start = pd.Timestamp(ev.start_date)
        status = np.where(rng.random(n) < 0.07, "cancelled", "confirmed")
        confirmed = np.flatnonzero(status == "confirmed")
        low, high = {"in-person": (0.15, 0.21), "hybrid": (0.18, 0.25), "virtual": (0.24, 0.30)}[ev.format]
        no_shows = rng.choice(confirmed, size=round(rng.uniform(low, high) * len(confirmed)), replace=False)
        attended = status == "confirmed"
        attended[no_shows] = False

        local = "Canada" if ev.city == "Toronto" else "United States"
        if ev.format == "virtual":
            country_p = None  # virtual events draw evenly from everywhere
        else:
            country_p = np.full(len(_COUNTRIES), 0.04)
            country_p[_COUNTRIES.index(local)] = 0.70
            country_p[_COUNTRIES.index("Canada" if local != "Canada" else "United States")] += 0.06
            country_p /= country_p.sum()

        frames.append(pd.DataFrame({
            "reg_id": [f"REG{next_id + k:06d}" for k in range(n)],
            "event_id": ev.event_id,
            "attendee_role": rng.choice(_ROLES[ev.event_type], n),
            "seniority": rng.choice(_SENIORITY, n, p=_SENIORITY_P),
            "industry": rng.choice(_ATTENDEE_INDUSTRIES[ev.event_type], n, p=(0.4, 0.2, 0.15, 0.15, 0.1)),
            "country": rng.choice(_COUNTRIES, n, p=country_p),
            "reg_date": (start - pd.to_timedelta(_registration_days_before(rng, n), unit="D")).date,
            "status": status,
            "attended": attended,
        }))
        next_id += n
    return pd.concat(frames, ignore_index=True)


def _sessions_engagement_feedback(
    rng: np.random.Generator, events: pd.DataFrame, regs: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sessions, engagement, feedback, next_id = [], [], [], 1
    attendees_by_event = regs[regs["attended"]].groupby("event_id")["reg_id"].apply(np.array)
    for ev in events.itertuples():
        attendees = attendees_by_event[ev.event_id]
        days = (ev.end_date - ev.start_date).days + 1
        per_day = rng.integers(8, 12) if ev.format == "virtual" else rng.integers(10, 16)
        for _ in range(int(days * per_day)):
            session_id = f"S{next_id:04d}"
            next_id += 1
            if ev.format == "hybrid":
                mode = str(rng.choice(FORMATS, p=(0.25, 0.15, 0.60)))
            else:
                mode = ev.format
            capacity = 1000 if mode == "virtual" else int(rng.choice((80, 120, 150, 200, 300, 500)))
            quality = float(np.clip(rng.normal(3.95, 0.45), 2.0, 4.9))
            if rng.random() < 0.08:  # the occasional flop
                quality = max(1.6, quality - 1.2)
            share = rng.beta(2, 12) * (1 + (quality - 3.9) * 0.3)
            attendance = int(min(capacity, len(attendees), max(5, share * len(attendees))))
            who = rng.choice(attendees, size=attendance, replace=False)

            if mode == "virtual":
                minutes = SESSION_MINUTES * rng.beta(1.5 + quality / 2, 1.5, attendance)
            elif mode == "hybrid":
                remote = rng.random(attendance) < 0.4
                minutes = np.where(remote, SESSION_MINUTES * rng.beta(1.5 + quality / 2, 1.5, attendance),
                                   rng.integers(25, SESSION_MINUTES + 1, attendance))
            else:
                minutes = rng.integers(25, SESSION_MINUTES + 1, attendance)
            online = mode != "in-person"
            engagement.append(pd.DataFrame({
                "event_id": ev.event_id,
                "session_id": session_id,
                "attendee_id": who,
                "watch_minutes": np.maximum(1, np.asarray(minutes)).astype(int),
                "polls": rng.poisson(1.2 if online else 0.4, attendance),
                "questions": rng.poisson(0.35 if online else 0.15, attendance),
                "booth_visits": 0 if mode == "virtual" else rng.poisson(0.6, attendance),
            }))

            n_feedback = max(1, int(rng.binomial(attendance, 0.18)))
            ratings = np.clip(np.rint(rng.normal(quality, 0.8, n_feedback)), 1, 5).astype(int)
            feedback.append(pd.DataFrame({
                "event_id": ev.event_id,
                "session_id": session_id,
                "rating": ratings,
                "comment": [_comment(rng, r) for r in ratings],
            }))
            sessions.append({
                "session_id": session_id,
                "event_id": ev.event_id,
                "track": rng.choice(_TRACKS[ev.event_type]),
                "speaker": f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
                "mode": mode,
                "capacity": capacity,
                "attendance": attendance,
                "rating": round(float(ratings.mean()), 2),
            })
    return (pd.DataFrame(sessions), pd.concat(engagement, ignore_index=True),
            pd.concat(feedback, ignore_index=True))


def _comment(rng: np.random.Generator, rating: int) -> str:
    if rng.random() < 0.4:  # many people leave a rating but no comment
        return ""
    pool = "positive" if rating >= 4 else "neutral" if rating == 3 else "negative"
    return str(rng.choice(_COMMENTS[pool]))


def _sponsors(rng: np.random.Generator, n: int = 30) -> pd.DataFrame:
    industries = list(_SPONSOR_INDUSTRIES)
    tiers = rng.choice(TIERS, n, p=_TIER_P)
    rows = []
    for i, (prefix, tier) in enumerate(zip(rng.permutation(_NAME_PREFIX)[:n], tiers), start=1):
        industry = industries[int(rng.integers(len(industries)))]
        low, high = _CONTRACT_RANGE[tier]
        goals = rng.choice(_GOALS, size=int(rng.integers(1, 3)), replace=False)
        rows.append({
            "sponsor_id": f"SP{i:03d}",
            "name": f"{prefix} {_NAME_SUFFIX[industry]}",
            "industry": industry,
            "tier": tier,
            "goals": "; ".join(goals),
            "contract_value": round(rng.uniform(low, high), -2),
        })
    return pd.DataFrame(rows)


def _sponsorships_and_leads(
    rng: np.random.Generator, events: pd.DataFrame, sponsors: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sponsorships, leads, next_id = [], [], 1
    for ev in events.itertuples():
        affinity = np.array([3.0 if ev.event_type in _SPONSOR_INDUSTRIES[ind] else 1.0
                             for ind in sponsors["industry"]])
        picked = rng.choice(len(sponsors), size=int(rng.integers(7, 13)), replace=False,
                            p=affinity / affinity.sum())
        sources, source_p = _LEAD_SOURCES[ev.format]
        for idx in sorted(picked):
            sp = sponsors.iloc[idx]
            package = (_VIRTUAL_PACKAGE if ev.format == "virtual" else _PACKAGE)[sp["tier"]]
            target = max(15, int(round(_PACKAGE_LEADS[package] * np.clip(ev.planned_size / 1000, 0.5, 2.0) / 5) * 5))
            sponsorships.append({"sponsor_id": sp["sponsor_id"], "event_id": ev.event_id,
                                 "package": package, "leads_target": target})
            # Performance varies around target; about a third fall short.
            actual = int(round(target * rng.lognormal(0.12, 0.3)))
            notes = rng.choice(_LEAD_NOTES, actual)
            leads.append(pd.DataFrame({
                "lead_id": [f"L{next_id + k:06d}" for k in range(actual)],
                "sponsor_id": sp["sponsor_id"],
                "event_id": ev.event_id,
                "source": rng.choice(sources, actual, p=source_p),
                "notes": np.where(rng.random(actual) < 0.3, "", notes),
            }))
            next_id += actual
    return pd.DataFrame(sponsorships), pd.concat(leads, ignore_index=True)


def _assign_people(rng: np.random.Generator, events: pd.DataFrame, regs: pd.DataFrame) -> pd.DataFrame:
    """Add person_id: about REPEAT_RATE of each event's registrants are past attendees of the
    same event type (roles differ by type), and they keep their role, seniority, industry and
    country. Everyone else is a new person."""
    profile = ["attendee_role", "seniority", "industry", "country"]
    regs = regs.copy()
    regs["person_id"] = ""
    pools: dict[str, pd.DataFrame] = {}  # event_type -> people who attended before (person_id + profile)
    next_id = 1
    for ev in events.sort_values("start_date").itertuples():
        rows = regs.index[regs["event_id"] == ev.event_id]
        pool = pools.get(ev.event_type, pd.DataFrame(columns=["person_id", *profile]))
        k = min(int(rng.binomial(len(rows), REPEAT_RATE)), len(pool))
        returning_rows = rng.choice(rows, size=k, replace=False) if k else np.array([], dtype=int)
        if k:
            people = pool.iloc[rng.choice(len(pool), size=k, replace=False)]
            regs.loc[returning_rows, "person_id"] = people["person_id"].to_numpy()
            regs.loc[returning_rows, profile] = people[profile].to_numpy()
        new_rows = rows.difference(returning_rows)
        regs.loc[new_rows, "person_id"] = [f"P{next_id + i:06d}" for i in range(len(new_rows))]
        next_id += len(new_rows)
        attended = regs.loc[rows][regs.loc[rows, "attended"]]
        pools[ev.event_type] = (pd.concat([pool, attended[["person_id", *profile]]])
                                .drop_duplicates("person_id").reset_index(drop=True))
    return regs


def generate_dataset(seed: int = DEFAULT_SEED) -> dict[str, pd.DataFrame]:
    """Build all eight tables, keyed by CSV file name."""
    rng = np.random.default_rng(seed)
    events = _events(rng)
    regs = _registrations(rng, events)
    sessions, engagement, feedback = _sessions_engagement_feedback(rng, events, regs)
    sponsors = _sponsors(rng)
    sponsorships, leads = _sponsorships_and_leads(rng, events, sponsors)
    # A separate stream, so adding people did not change any other table.
    regs = _assign_people(np.random.default_rng([seed, 1]), events, regs)
    return {
        "events.csv": events.drop(columns="planned_size"),
        "sponsors.csv": sponsors,
        "sponsorships.csv": sponsorships,
        "registrations.csv": regs,
        "sessions.csv": sessions,
        "engagement.csv": engagement,
        "leads.csv": leads,
        "feedback.csv": feedback,
    }


def write_dataset(tables: dict[str, pd.DataFrame], out_dir: str | Path) -> list[Path]:
    """Write each table to ``out_dir`` (dates as YYYY-MM-DD) and return the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in tables.items():
        path = out_dir / name
        df.to_csv(path, index=False, date_format="%Y-%m-%d", lineterminator="\n")
        paths.append(path)
    return paths


def ensure_dataset(directory: str | Path, seed: int = DEFAULT_SEED) -> Path:
    """Generate the dataset into ``directory`` unless it already has events.csv."""
    directory = Path(directory)
    if not (directory / "events.csv").exists():
        write_dataset(generate_dataset(seed), directory)
    return directory
