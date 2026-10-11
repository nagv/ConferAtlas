# Prompt log

Every prompt used to build ConferAtlas with Claude Code, in order and word for word. Pasted content is shown in quote blocks and notes are in italics.

## Session 1 (11 Oct 2026)

1. > I want to build new Streamlet application named ConferAtlas: an AI-powered conference management platform used by conference executors to show past conference performance to sponsors. Set up a Streamlit frontend with pandas and Plotly, and LangChain for AI features. Create a clean folder structure (app/, app/views/, app/components/, core/ for analysis logic, data/, tests/), a requirements.txt, a .env.example for the LLM API key, and a README with setup steps. Keep calculation logic separate from UI code

   *Result: initial scaffold with three pages (portfolio overview, sponsor report, AI insights), core/ logic and tests.*

   *(`/compact` was run here. It's a command, not a prompt.)*

2. > where is the project

3. > can you have the project at "C:\Users\rajvo\Gen Academy"

   *Result: project moved to `C:\Users\rajvo\Gen Academy\ConferAtlas`.*

4. > whats purpose of .venv

5. > Application not running

   *Pasted with it: PowerShell's "streamlit is not recognized" error. Result: created `.venv` with uv, because there was no usable system Python.*

6. > do we need to have virtual enviornment mandatorily for the streamlet to run?

7. > nothing is running on port 8501

8. > I noticed some issues per UI testing. There is only 1 sponsor for a conference. Also, explain me the purpose of Leads per Sponsor

9. > just to clarify your test data. Each conference is sponsored by more than 1 Sponsor. is this correct?

10. > Write a data generator script with a fixed random seed that creates realistic synthetic CSVs for 12 past conferences across tech, business, medical and education event types, in in-person, virtual and hybrid formats, across 6 cities. Create: events.csv, sponsors.csv, sponsorships.csv, registrations.csv, sessions.csv, engagement.csv, leads.csv and feedback.csv, with the columns described below. Include realistic patterns: early-bird registration spikes, 15–30% no-shows, varied session ratings, some sponsors missing their lead targets, and a mix of positive and negative feedback comments. Add validation that checks required columns, data types and date formats, and shows clear errors.

    *The column list was missing, so Claude asked for it.*

11. > here is the column list per csv

    *Pasted with it:*

    | CSV | Key columns |
    |---|---|
    | events.csv | event_id, name, event_type, format (in-person/virtual/hybrid), city, start_date, end_date, budget, actual_cost |
    | sponsors.csv | sponsor_id, name, industry, tier, goals, contract_value |
    | sponsorships.csv | sponsor_id, event_id, package, leads_target |
    | registrations.csv | reg_id, event_id, attendee_role, seniority, industry, country, reg_date, status, attended |
    | sessions.csv | session_id, event_id, track, speaker, mode, capacity, attendance, rating |
    | engagement.csv | event_id, session_id, attendee_id, watch_minutes, polls, questions, booth_visits |
    | leads.csv | lead_id, sponsor_id, event_id, source (booth/demo/virtual), notes |
    | feedback.csv | event_id, session_id, rating, comment |

    *Result: `core/generator.py`, `core/schema.py` (validation) and `scripts/generate_data.py`.*

12. > yes

    *This answered "switch the app over to the new dataset?" Result: the app was rebuilt on the eight CSVs, with a new Event report page and per-sponsor reports.*

13. > can you list all the prompts I used in this session so far

14. > yes.. keep savings the prompts to docs

    *Result: this file was created, plus a CLAUDE.md rule to keep adding to it.*

15. > Create a core/analysis.py module with pure pandas functions for all metrics: portfolio KPIs (events, registrations, show-up rate, NPS, sponsor revenue, leads), per-event KPIs, session performance, audience breakdowns, sponsor scorecards (leads vs. target, cost per lead), and period-over-period comparison. Every function takes a filtered dataframe and returns typed results. Write pytest tests for each function, including edge cases like empty data and zero baselines.

    *Result: `core/analysis.py` with typed functions for portfolio KPIs, per-event KPIs, session and track performance, audience breakdowns, sponsor scorecards and period-over-period comparison, plus `filter_dataset()`. It replaced `core/metrics.py`, and the app and AI now use it. `tests/test_analysis.py` has 38 test functions (42 cases), including empty-data and zero-baseline cases.*

16. > Build a Streamlit sidebar with global filters that apply to every page: date range (quick picks such as last 6 months or last year, plus custom dates), event type, format, city, event (cascading: narrows based on the earlier selections) and an optional sponsor. Add an Apply filters button, a Reset filters button, and a “Current selection” summary card. Store filter state in st.session_state and cache filtered data with st.cache_data.

    *Result: global sidebar filters (date presets plus custom dates, then cascading type, format, city and event, and an optional sponsor) with Apply, Reset and a "Current selection" card. The logic is in `core/filters.py` and the UI in `app/components/filters.py`. Applied filters are kept in `st.session_state["filters"]` and filtered data is cached with `st.cache_data`. Every page uses the filtered data and shows a message when nothing matches. 23 new tests, including headless UI tests that click Apply and Reset.*

17. > Create the Portfolio Overview page: KPI cards (events, registrations, show-up rate, average NPS, sponsor revenue, leads), each showing change vs. the previous equal period. Add Plotly charts: registrations vs. attendance by event (grouped bar), registration trend over time (line), and event type mix (donut). Add a sortable event scorecard table, plus page-level options for sort-by and top N. Use a clean, professional theme with consistent colours.

    *Result: rebuilt the Portfolio overview with 6 KPI cards that compare against the previous equal period (shown when a date range is picked), plus three charts: registrations vs attendance (grouped bar), monthly registration trend (line) and event type mix (donut). It also has an event scorecard with sort-by, order and top-N options. New `app/components/theme.py` gives one palette and a Plotly template for every page. New analysis functions: `registrations_over_time`, `event_type_mix`, `rank_events` and an average-of-events NPS. 122 tests pass.*

18. > Create two more pages. Event Drill-down: pick one event and show its KPIs, registration curve by days before the event, session attendance vs. capacity, in-person vs. virtual engagement, and a session table with filters for track, mode, speaker and rating range. Audience Insights: role and seniority breakdown, industry mix, attendees by country (map), new vs. repeat attendees, with filters for role, seniority, industry and attendance status.

    *Result: the Event report became **Event drill-down** (KPIs, registration curve, session fill rate vs capacity, engagement by session mode, and a session table with track, mode, speaker and rating filters; sponsors and feedback kept as tabs). New **Audience insights** page with role and seniority bars, an industry treemap, a world map by country, and new vs returning attendees, with role, seniority, industry and attendance-status filters. Added an optional `person_id` column to registrations, since repeat attendance can't be measured without it. The generator fills it using a separate random stream, so the other seven files are unchanged. 135 tests pass.*

19. *(Pasted error only, no prompt text):*

    > ImportError: cannot import name 'breakdown_bars' from 'app.components.charts' (...\app\components\charts.py)
    > File "...\app\main.py", line 20 ... File "...\app\views\audience.py", line 6

    *Result: no code change needed. The Streamlit server running in the user's terminal had been started before the new pages were added and still held the old `charts.py` in memory. Fix: restart it. Claude's background server on the same port was stopped to avoid confusion.*

20. > is the service running on 8501 or 8502

    *Answer: the user's server was on 8502 (started at 10:33, when Claude's copy already held 8501). Nothing is on 8501 now. The 8502 server still has the stale code and needs a restart.*

21. > nothing running on 8501 port

    *Result: Claude stopped the stale 8502 server and started a fresh one on 8501 in a terminal tab, from the project folder so `.streamlit/config.toml` (headless mode, theme) is picked up. The Audience insights page loads without the import error.*

22. > Create the Sponsor Performance page (sales analytics for sponsors): KPI cards for contract value, leads vs. target, cost per lead, booth visits and renewal status; charts for leads vs. target by sponsor, lead source mix (booth, demo, virtual) and revenue by sponsor tier; and a sponsor scorecard table. Add filters for sponsor tier, industry, package and lead source. Highlight sponsors below 80% of their lead target.

    *Result: new **Sponsor performance** page with filters for tier, industry, package and lead source. It has 6 KPI cards (contract value, leads vs target, cost per lead, booth visits, renewal rate, sponsorships below 80%), charts for leads vs target by sponsor (colour bands plus an 80% tick), lead source mix and revenue by tier, a scorecard with red rows for sponsors below 80%, and a table of below-80% sponsorships. Renewal status and per-sponsor booth visits aren't in the data, so renewal is derived from sponsorship years and booth visits are shown as event-wide traffic, both labelled as such. Neutral KPI deltas no longer show arrows on any page. 143 tests pass.*

23. > Create the Feedback Analyzer page. Show rating distribution, sentiment by event and top themes. Add an Analyse feedback button that sends the currently filtered comments (batched, with a token limit) to an LLM through LangChain and returns structured output using a Pydantic model: themes (name, count, example quote), overall sentiment, and top 3 action items. Display the results as cards and a table of comments tagged with theme and sentiment. Handle missing API keys, timeouts and invalid model output gracefully.

    *Result: new **Feedback analyzer** page with a rating distribution, sentiment by event, keyword themes, and an **Analyse feedback** button. New `core/ai/feedback.py` collapses duplicate comments (3,604 comments are only 20 distinct texts), batches them under token limits, and uses LangChain structured output with Pydantic models (themes with name/count/quote, overall sentiment, top 3 actions, per-comment tags). Theme counts are computed from the tags. It handles a missing API key (button disabled), timeouts (60 s per request, partial results kept) and invalid output (one repair retry). Switched structured output to `method="json_schema"` after LangChain warned that tool calling isn't supported on claude-opus-5-5. 168 tests pass, all offline using a fake model.*

24. > Create the Ask the Portfolio chat page using a LangChain tool-calling agent. Give it fixed tools that wrap the existing analysis functions: portfolio_summary, event_kpis, audience_mix, compare_events, sponsor_outcomes and make_chart. Tools must respect the global filters. Rules: every number in an answer must come from a tool result; no raw attendee records are sent to the model; charts can only use computed tool results. Show a “View calculations” expander under each answer and suggested starter questions. Limit tool calls, set timeouts and cap conversation history.

    *Result: new **Ask the portfolio** page backed by `core/ai/portfolio_agent.py`, a tool-calling loop on LangChain `bind_tools` with 6 fixed tools over the filtered data. The rules are enforced in code: tool results go into a ledger, every number in an answer is checked against it (one rewrite, then a visible warning), payloads are aggregate-only and guarded against record IDs, and `make_chart` only accepts ledger results. Each answer has a "View calculations" expander, and starter questions show when the chat is empty. Limits: 6 tool calls and 5 turns per question, 45 s per request, a 120 s deadline, and the last 6 messages of history. Fixed a duplicate-chart crash found by the tests. 202 tests pass, all offline.*

25. > Create a reusable Sponsor Report Writer skill: a prompt template plus instructions, a fixed output structure (executive summary, audience reached, engagement, leads vs. target, highlights, recommendations), one example report and a checklist (only use tool-provided numbers, cite the event names). Add a Generate sponsor summary button on the Sponsor Performance page that uses this skill for the selected sponsor and filters, and lets the user download the report as Markdown.

    *Result: new skill folder `skills/sponsor-report-writer/` (SKILL.md with frontmatter, instructions, fixed six-section structure and checklist; prompt.md template; example_facts.json and example_report.md). `core/ai/skills.py` loads it and `core/ai/sponsor_report.py` builds the sponsor fact sheet, generates the report, enforces the checklist in code with one revision, and falls back to a template draft without a key. The Sponsor performance page gained a sponsor picker, a **Generate sponsor summary** button, the checklist results, the rendered report and a Markdown download. A test makes sure the example report passes its own checklist. 216 tests pass.*

26. > Create a reusable Sponsor Report Writer skill: ... *(identical to prompt 25, sent again)*

    *Result: no changes. Prompt 25 had already been implemented, so Claude confirmed what exists and asked whether anything should change.*

27. > Add an AI usage panel to the sidebar showing, for each LLM call, input and output tokens, estimated cost and latency, plus session totals. Add retry with backoff, a request timeout, a max output token limit and friendly error messages. Make sure the dashboards work fully without an API key, and the AI pages explain what’s missing.

    *Result: new `core/ai/llm.py` is the single path for every LLM call. It sets a request timeout, an output-token cap and retry with exponential backoff and jitter (honouring Retry-After, with the provider's built-in retries off), sorts errors into friendly, actionable messages, and records per-call usage through a LangChain callback. New sidebar **AI usage** panel shows each call's tokens, estimated cost, latency and status, plus session totals. Prices come from `.env` and are never guessed. A shared notice on every AI page explains what's missing and what still works, and a test confirms every page works without an API key. Fixed an eager-default bug in the usage callback found by the tests. 251 tests pass.*

28. > Review the whole app as a sponsor seeing it for the first time. Improve the UX: consistent page headers with a one-line description, active-filter chips at the top of each page, loading spinners, empty-state messages when filters return no data, readable chart labels, number formatting (K/M, percentages) and a mobile-friendly layout. Add an About section explaining that the data is synthetic.

    *Result: UX pass from a first-time sponsor's point of view. Added a shared page header (icon, title, one-line description) with active-filter chips and a "Synthetic demo data" chip on every page. Navigation is grouped (Dashboards / AI assistants / Help), with a new **About** page explaining the synthetic data and how each metric is calculated. The sidebar was reordered: brand and About link, then filters, then a collapsed Data source section, then AI usage. Uploads are now read before the filters. Other changes: empty states with a one-click "Reset all filters", a page-level loading spinner, compact K/M numbers with exact values on hover, event names shortened at word boundaries but keeping the year (with a uniqueness fallback), a simpler chart toolbar, and mobile CSS. Checked on desktop and phone viewports. 273 tests pass.*

29. > Add tests for filters, analysis functions, agent tool limits and failure states (no API key, empty data, invalid CSV). Run them and fix any failures. Update the README with: project purpose, users (executors, sponsors, attendees), feature list per page, screenshots placeholders, setup and run steps, and the planned roadmap (RAG, multi-agent, fine-tuning, evals, security).

    *Result: 55 new tests (328 total). New `tests/test_failure_states.py` drives every page with invalid uploads, headers-only datasets, impossible filters and the CLI; new `tests/test_consistency.py` cross-checks analysis functions and filters on the full dataset; and there are new agent-limit tests (tool budget across turns, payload size, empty selections). The tests found and fixed three crashes: empty uploaded files, non-UTF-8 or binary files (Excel's Windows-1252 and UTF-8-BOM CSVs are now accepted), and the About page on an empty dataset. README rewritten with purpose, users (executives/organisers, sponsors, attendees), features by page, screenshot placeholders (`docs/screenshots/`), setup and run steps (venv and uv), configuration, architecture, calculations, AI guardrails, testing, troubleshooting, and a roadmap covering RAG, multi-agent, fine-tuning, evals and security.*

30. > I want to use OpenRouter key for connecting to LLM. I have OPENROUTER_API_KEY key and LLM model I want to use is google/gemini-2.5-flash. Use this code snippet at reference to initialize and use the model *(pasted a `get_llm()` helper calling `init_chat_model(model=..., model_provider="openai", base_url=OPENROUTER_BASE_URL, api_key=api_key(), temperature=0, max_tokens=1000, max_retries=3)`, noting that max_tokens avoids 402 errors on low-credit accounts)*. Change the .env entries accordingly

    *Result (partial; the usage limit was reached): added an `openrouter` provider (OpenAI-compatible via `init_chat_model(model_provider="openai", base_url=OpenRouter, api_key=OPENROUTER_API_KEY, temperature=0, max_tokens)`), now the default with `google/gemini-2.5-flash`. Added a 402 "insufficient credit" message, tool-calling structured output for OpenAI-compatible providers, langchain-openai in requirements, `.env` with a key placeholder, an updated `.env.example`, and tests isolated from `.env`. Still to do: update the tests that expect Anthropic defaults, add OpenRouter tests, run the full suite, update the README, and do a live check once the key is added.*


## Session 2 (11 Oct 2026)

1. > Analyze the project

    *Result: reviewed architecture, data validation, AI safeguards and tests; identified unfinished OpenRouter migration and other follow-up issues.*

2. > first fix OpenRouter migration
   > I want to use OPENROUTER_BASE_URL=[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)  

    *Result: made OPENROUTER_BASE_URL configurable, set the requested plain URL in .env and .env.example while preserving the API key, and aligned provider tests and documentation with OpenRouter/Gemini defaults.*

31. > I want to use OPENROUTER_BASE_URL=https://openrouter.ai/api/v1


3. > how to restart the app

    *Result: provided the stop-and-start steps for the Streamlit app.*

4. > stop the server which is running

    *Result: identified and stopped the ConferAtlas Streamlit server processes.*

5. > can you fix remaining high priority findings

    *Result: strengthened signed-number and unit verification; removed questions and arguments as numeric evidence; added event/session, registration and sponsorship relationship validation; invalidated stale AI drafts; added common-contact redaction and review acknowledgement for real feedback; added staff-only OIDC access with an allowlist, expiry checks and local-only demo binding. Identity-provider credentials must be configured separately.*

6. > Staff only; sponsors receive exported reports (recommended)

    *Result: implemented staff-only access; sponsors receive exported reports rather than app accounts.*

    *Validation: the final full suite had 376 passes and one feedback-page timeout; the timed-out test passed on an isolated rerun. The earlier full suite passed all 370 tests collected before the final regression cases were added. No live LLM or OIDC requests were made.*

    *Result: `OPENROUTER_BASE_URL` is read from `.env` (default `https://openrouter.ai/api/v1`, trimmed of spaces and a trailing slash), and `.env` and `.env.example` both set it. Finished the OpenRouter work started under prompt 30: removed a duplicate entry in test isolation, added a test for the 402 "not enough credit" error (not retried), and added a README troubleshooting entry. Live checks with the user's key through OpenRouter and google/gemini-2.5-flash all worked: a plain reply, structured output, the portfolio chat agent (it called `portfolio_summary` and every figure verified) and feedback analysis (1 batch, 812 output tokens). Full suite: 358 tests; the only failures were 60-second AppTest timeouts while another pytest run on the same project was competing for the machine, and those tests pass when re-run.*

7. > can you add UI/UX tweaks to make the UI richer. I am good with the functionality, font selection and navigations etc. Only looking for some professional theam/background

    *Result: added a light slate canvas with subtle blue/teal gradients, white cards, coordinated chart neutrals, softer borders and shadows, and refined control surfaces. Kept fonts and dashboard behavior. All 36 component and page smoke checks passed.*

8. > I am thinking we can have a landing page on what this platform about 

    *Result: added a Welcome page as the default entry, explaining the platform, its audience and three main benefits, with links into the existing workflows. Preserved staff access controls. All 19 welcome, page and access checks passed; checked desktop and narrow-screen previews.*

32. > commit all the changes to git

    *Result: there was no git repository, so Claude created one (`main` branch) and made the first commit (78 files) with all the work, including the other session's auth, access and home-page files. Before committing it checked that `.env` (with the real OpenRouter key) and the virtual environment are ignored, that test files hold only dummy keys and `secrets.toml.example` only placeholders, and added the scratch test-run folders (`.pytest-*`) to `.gitignore`. Not pushed: no remote is configured.*

33. > git name to should be Nagaraju Voruganti

    *Result: set `user.name = Nagaraju Voruganti` for this repository (email unchanged) and rewrote the unpushed commits so they show that author. The global git name was left as it was.*

34. > done..commit and pus the changes to main

    *Result: there was no remote, so Claude asked about visibility; the user chose public. Claude re-scanned the tracked files for secrets (none; `.env` is not tracked), committed this log entry, created the public repository nagv/ConferAtlas with `gh`, and pushed `main`.*

35. > I dont see all screenshots got added. can you add the missing screenshots

    *Result: 7 of the 8 screenshots the README references were missing (only `welcome.jpg` existed). Claude captured overview, event drill-down, sponsor performance, audience, feedback, ask and mobile from the running app with headless Microsoft Edge (already installed, so nothing was downloaded), checked each image, and re-captured three: audience in a taller window so the map shows, ask with more render time so all starter questions appear, and mobile at 480 px because 390 px clipped. Recapture instructions were added to `docs/screenshots/README.md`. Committed and pushed to main.*

36. > push the updates to git

    *Result: nothing was pending; `main` already matched origin/main (the screenshots were pushed with commit f9b6c54). Only this log entry was committed and pushed.*

37. > how about the docs/screenshots/README.md you updated

    *Answer: it was already committed and pushed in f9b6c54 together with the screenshots. GitHub's copy includes the new "Recapturing" section, and there are no local differences.*

38. > Create a plan to refactor the code to have front end and backend logic similar to C:\Users\rajvo\Gen Academy\community-health-pulse
    > Dont change any functionality. Have this deployed as 2 applications (Front end is Streamlet and backend is Fast API).
    > Make sure not one of existing functionality is missing/broken in the proposal

    *Result: Claude asked three questions (deployment: Docker Compose plus local scripts; auth: sign-in stays in the frontend plus an API token; API: one endpoint per page) and the plan was approved. Before changing code, Claude recorded 87 page snapshots from the original app as a parity gate. The app is now two applications: `backend/` (FastAPI: in-memory datasets, one endpoint per page, AI endpoints that return their usage records, and the `X-API-Token` check) and `frontend/` (Streamlit, presentation only, through `frontend/client.py`), with contracts in `shared/`. `core/` is unchanged apart from moving small pure pieces into `shared/`. Added Dockerfiles, `compose.yaml`, `run_local.py`, `start-*.ps1`, per-app `.env.example` files and requirements, and README updates. All existing tests were ported, and new API, client, serialization, shared and architecture tests were added. All 87 page snapshots match the original exactly. Live checks with OpenRouter passed for chat, the sponsor report and feedback analysis. Docker was not installed on this machine, so the Compose build was not run.*

39. > where is the frontend and backend running in local

    *Answer: both run from one terminal via `run_local.py`: the Streamlit frontend at http://localhost:8501 and the FastAPI backend at http://localhost:8000 (API docs at http://localhost:8000/docs). Both were checked and responding.*

40. > push the changes to git

    *Result: committed the prompt-log entries and pushed the `refactor/frontend-backend` branch to GitHub. `main` was not changed.*

41. > go ahead and merge

    *Result: merged `refactor/frontend-backend` into `main` (merge commit, no conflicts; `main` had no other changes) and pushed `main` to GitHub.*

42. > i am creating app in Streamlet cloud. whats main file path

    *Answer: `frontend/app.py`. Streamlit Community Cloud runs only the frontend, so the FastAPI backend must be hosted somewhere else, and `BACKEND_URL`, `BACKEND_API_TOKEN` and `CONFERATLAS_REQUIRE_AUTH=true` go in the app's Secrets.*

43. > lets first deploy backend to Render first. give me instructions

    *Answer: step-by-step Render setup: a Docker web service built from `backend/Dockerfile` with the repository root as the build context, `PORT=8000`, `/health` as the health check, the API token and LLM settings as environment variables, then checks with `/health`, `/docs` and a token-protected `/status` call.*

44. > [screenshot of https://conferatlas.onrender.com showing {"detail": "Not Found"}] backend deploy succeed. but giving error

    *Answer: the 404 is expected, because the API has no page at `/`; `/health` and `/docs` both responded. The check did find two missing Render environment variables: `/status` answered without a token (so `BACKEND_API_TOKEN` is not set) and `llm_configured` was false (so `OPENROUTER_API_KEY` is not set).*

45. > i added few as secrets.. does this pose any issues

    *Answer: yes. Render Secret Files are written to `/etc/secrets/<name>`, not set as environment variables, and the backend only reads environment variables (plus `.env` in the project folder). The live check still showed no API key and no token check. The fix is to add them as Environment Variables, which Render already stores encrypted and hides in the dashboard.*

46. > backend got deployed at https://conferatlas.onrender.com/. Give me environment variables required for front end

    *Answer: checked the backend first (key configured, requests without the token refused with 401). Gave the Streamlit Cloud Secrets: `BACKEND_URL` and `BACKEND_API_TOKEN` (required), `AI_HTTP_TIMEOUT_S` (optional), and the staff sign-in settings (`CONFERATLAS_REQUIRE_AUTH`, `CONFERATLAS_STAFF_EMAILS`, `[auth]`) for a private app, plus what a public demo without sign-in would mean.*

47. > is environment values in auth are required? if yes, give me exact values

    *Answer: only needed for a staff-only app; a public synthetic demo can leave `[auth]` out. Claude cannot know the exact client ID and secret, which come from the identity provider. It gave the fixed values (Google metadata URL, redirect URI pattern), how to generate the cookie secret, and the Google Cloud steps to create the client.*

48. > i would like defer google authentication for later..

    *Answer: deploy without sign-in, with only `BACKEND_URL` and `BACKEND_API_TOKEN` in the Streamlit Cloud Secrets. The synthetic demo and AI features are then public; uploads stay blocked until sign-in is configured. Sign-in can be added later by adding the secrets and rebooting, with no code change.*

49. > i  am getting this error part of deploy 
    > Staff sign-in must be configured before this app can show data. Ask the app administrator to complete the authentication setup in the README.

    *Answer: the access rule requires sign-in whenever the app is not served from the local machine (Streamlit Cloud is remote), so running there without sign-in is blocked by design, as it was before the split. The earlier advice that a public demo could skip sign-in was wrong. Claude offered an explicit opt-in public demo setting (built-in synthetic data only) or setting up Google sign-in now, and asked which the user wants.*

50. > [chose: Opt-in public demo (Recommended)]

    *Result: added `CONFERATLAS_PUBLIC_DEMO=true`, an explicit opt-in that lets a remote frontend show the built-in synthetic data without sign-in. It is off by default, uploads are hidden, a custom data folder or `CONFERATLAS_REQUIRE_AUTH=true` still require sign-in, and everything else behaves as before. Tests added; all 529 pass. Committed and pushed to main.*
