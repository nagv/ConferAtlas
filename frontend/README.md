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

## Public synthetic demo: Streamlit Community Cloud + Render

Use `frontend/app.py` as the Streamlit entry point. In the Cloud app's **Settings → Secrets**, use
[`public-demo.secrets.toml.example`](.streamlit/public-demo.secrets.toml.example), replacing the Render
service URL and API token. The token must match `BACKEND_API_TOKEN` in the Render service's environment.
These are frontend settings: setting them only on Render does not configure Streamlit.

Keep `BACKEND_URL`, `BACKEND_API_TOKEN`, `CONFERATLAS_PUBLIC_DEMO` and `CONFERATLAS_REQUIRE_AUTH` at the
**top level**, before any `[auth]` section or other TOML table. Set `CONFERATLAS_PUBLIC_DEMO = true` and
`CONFERATLAS_REQUIRE_AUTH = false`. Public demo mode does not need OIDC credentials.

On Render, leave `CONFERATLAS_DATA_DIR` unset to select the built-in synthetic dataset. A custom data
folder requires staff sign-in even when public demo mode is enabled. Keep model keys on Render only.

Save the Cloud secrets and reboot the Streamlit app after deploying the latest code. If a browser session
previously held an uploaded dataset, open a fresh session for the public demo.

If **“Staff sign-in must be configured”** still appears, check:

- The deployed revision includes `public_demo_enabled` in `shared/access.py` and reads settings through
  `client.setting` in `frontend/components/auth.py`.
- `CONFERATLAS_PUBLIC_DEMO` is a top-level boolean `true`, and `CONFERATLAS_REQUIRE_AUTH` is `false`.
- The Render backend uses the built-in data folder, and the browser session has no uploaded data.

Then check the Render service's `/health` endpoint. Once the access guard passes, an invalid backend URL
or mismatched API token will produce a separate connection or authorization error. Public demo visitors
can use AI features if a provider key is configured on the backend, consuming its credit.
