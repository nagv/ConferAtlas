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
