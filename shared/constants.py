"""Fixed vocabularies the frontend needs without importing the backend's data code.

``tests/test_shared.py`` checks each one against its source of truth in ``core``.
"""
DATASET_FILES = ("events.csv", "sponsors.csv", "sponsorships.csv", "registrations.csv", "sessions.csv",
                 "engagement.csv", "leads.csv", "feedback.csv")
SENIORITY_ORDER = ("Entry", "Mid", "Senior", "Director", "VP", "C-level")
