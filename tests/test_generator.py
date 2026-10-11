import pandas as pd
import pytest

from core.generator import CITIES, EARLY_BIRD_DAYS, generate_dataset, write_dataset
from core.schema import SCHEMAS, load_dataset


@pytest.fixture(scope="module")
def tables():
    return generate_dataset(seed=42)


@pytest.fixture(scope="module")
def loaded(tables, tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    write_dataset(tables, out)
    return load_dataset(out)


def test_same_seed_gives_identical_data(tables):
    again = generate_dataset(seed=42)
    for name, df in tables.items():
        pd.testing.assert_frame_equal(df, again[name])


def test_different_seed_gives_different_data(tables):
    other = generate_dataset(seed=7)
    assert not tables["registrations.csv"].equals(other["registrations.csv"])


def test_all_files_have_schema_columns_and_validate(tables, loaded):
    for name, schema in SCHEMAS.items():
        assert list(tables[name].columns) == [c.name for c in schema]
    assert set(loaded) == set(SCHEMAS)


def test_event_mix(loaded):
    events = loaded["events.csv"]
    assert len(events) == 12
    assert set(events["event_type"]) == {"tech", "business", "medical", "education"}
    assert set(events["format"]) == {"in-person", "virtual", "hybrid"}
    assert set(events["city"]) == set(CITIES) and len(CITIES) == 6
    assert (events["start_date"] < pd.Timestamp("2026-10-11")).all()


def test_no_show_rate_between_15_and_30_percent(loaded):
    confirmed = loaded["registrations.csv"].query("status == 'confirmed'")
    no_show = 1 - confirmed.groupby("event_id")["attended"].mean()
    assert no_show.between(0.15, 0.30).all(), no_show


def test_early_bird_spike(loaded):
    regs = loaded["registrations.csv"].merge(loaded["events.csv"][["event_id", "start_date"]])
    days_before = (regs["start_date"] - regs["reg_date"]).dt.days
    just_before_deadline = days_before.between(EARLY_BIRD_DAYS, EARLY_BIRD_DAYS + 5).sum()
    week_after_deadline = days_before.between(EARLY_BIRD_DAYS - 6, EARLY_BIRD_DAYS - 1).sum()
    assert just_before_deadline > 2 * week_after_deadline


def test_some_sponsors_miss_lead_targets(loaded):
    actual = loaded["leads.csv"].groupby(["sponsor_id", "event_id"]).size().rename("actual")
    joined = loaded["sponsorships.csv"].set_index(["sponsor_id", "event_id"]).join(actual).fillna(0)
    missed = (joined["actual"] < joined["leads_target"]).mean()
    assert 0.1 < missed < 0.6


def test_session_ratings_vary_and_match_feedback(loaded):
    sessions = loaded["sessions.csv"]
    assert sessions["rating"].std() > 0.3
    means = loaded["feedback.csv"].groupby("session_id")["rating"].mean().round(2)
    assert (sessions.set_index("session_id")["rating"] - means).abs().max() < 0.01


def test_engagement_matches_session_attendance(loaded):
    counts = loaded["engagement.csv"].groupby("session_id").size()
    assert (loaded["sessions.csv"].set_index("session_id")["attendance"] == counts).all()


def test_feedback_has_positive_and_negative_comments(loaded):
    fb = loaded["feedback.csv"].dropna(subset=["comment"])
    assert fb.loc[fb["rating"] >= 4, "comment"].nunique() >= 5
    assert fb.loc[fb["rating"] <= 2, "comment"].nunique() >= 5


def test_people_return_with_a_consistent_profile(loaded):
    regs = loaded["registrations.csv"].merge(loaded["events.csv"][["event_id", "event_type", "start_date"]])
    assert not regs.duplicated(["person_id", "event_id"]).any()
    profile = ["attendee_role", "seniority", "industry", "country", "event_type"]
    assert (regs.groupby("person_id")[profile].nunique() <= 1).all().all()
    events_per_person = regs.groupby("person_id")["event_id"].nunique()
    assert 0.1 < (events_per_person > 1).mean() < 0.4
