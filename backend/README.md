# ConferAtlas backend (FastAPI)

Owns the data, every calculation (`core/`) and every AI call. The Streamlit frontend only presents what it returns.

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000   # from the repository root
```

- Settings: `backend/.env` (see `.env.example`); a root `.env` is read as a fallback.
- Auth: when `BACKEND_API_TOKEN` is set, every route except `/health` needs the `X-API-Token` header.
- State: datasets are held in memory (`store.py`), so run one worker. The built-in demo data is id `builtin`.
- Contracts: request and response models live in `shared/models.py`; interactive docs at `/docs`.
- Docker: `docker build -f backend/Dockerfile .` (build context is the repository root).

## Render Python web service

Deploy from the repository root (leave Render's **Root Directory** empty). The backend imports `core`
and `shared`, so deploying only the `backend/` folder is insufficient.

- Build command: `pip install -r backend/requirements.txt`
- Start command: `python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health check path: `/health`
- Environment: set `BACKEND_API_TOKEN` to the same value used by the frontend; keep AI keys here.
- Public synthetic demo: leave `CONFERATLAS_DATA_DIR` unset. A custom folder requires staff sign-in.

Copy the service's public URL from the Render dashboard into Streamlit Cloud's `BACKEND_URL` secret.
`GET /health` must return HTTP 200 with `status: "ok"`. `/status` returns HTTP 401 without a token when
`BACKEND_API_TOKEN` is set, and HTTP 200 with the correct `X-API-Token` header. If both `/health` and
`/status` return plain-text HTTP 404, confirm the service URL and successful deployment of this FastAPI
app before debugging the frontend. A 404 at `/` alone is normal: no root route is defined.
