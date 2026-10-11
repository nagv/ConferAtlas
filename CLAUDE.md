# ConferAtlas

## Architecture
Two apps: `backend/` (FastAPI; owns data, `core/` calculations and all AI calls) and `frontend/` (Streamlit; presentation only, calls the API through `frontend/client.py`). Contracts live in `shared/models.py`. The frontend must never import `core`, `backend` or LangChain (enforced by `tests/test_architecture.py`), and `tests/test_parity.py` must stay green: pages must look exactly as recorded unless a visible change is intended.

## Prompt log
After each user prompt, append it word for word to `docs/prompts.md` under the current session heading. Start a new `## Session N (date)` heading for each new session. Show pasted content in quote blocks, add a short italic *Result:* note when the prompt changed the project, and keep the user's original spelling.
