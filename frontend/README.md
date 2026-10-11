# ConferAtlas frontend (Streamlit)

Pages, widgets, charts, staff sign-in and the AI usage panel. No analysis code and no model keys: every figure
comes from the backend API through `client.py`.

```bash
cd frontend
streamlit run app.py          # the backend must be running (BACKEND_URL, default http://127.0.0.1:8000)
```

- Settings: `frontend/.env` (see `.env.example`): `BACKEND_URL`, `BACKEND_API_TOKEN`, staff sign-in.
- Staff sign-in: `frontend/.streamlit/secrets.toml` (see `secrets.toml.example`).
- Caching: page responses are cached for 5 minutes per dataset, filters, date and page options.
- Docker: `docker build -f frontend/Dockerfile .` (build context is the repository root).
