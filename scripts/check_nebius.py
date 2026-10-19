"""Check the Nebius setup: key, base URL, model list, an embedding call and a chat call.

Reads the same settings files as the backend (backend/.env, then .env at the project root) and never
prints the key.

    python scripts/check_nebius.py                         # check the key and list the models
    python scripts/check_nebius.py --chat MODEL_ID         # also test a chat model
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai.llm import classify_error  # noqa: E402
from core.config import get_settings  # noqa: E402

EMBEDDING_HINTS = ("embed", "bge", "e5-", "gte-")
HELP = {
    "auth": "The key was rejected. Paste NEBIUS_API_KEY again (no quotes or spaces) and check your credit is active.",
    "not_found": "Not found. NEBIUS_BASE_URL must end at /v1, and the model id must match the list exactly.",
    "network": "Couldn't reach Nebius. Check NEBIUS_BASE_URL for typos and your internet connection.",
    "bad_request": "Nebius refused the request. For embeddings, pick a model from the EMBEDDING list.",
    "credits": "The account has no credit left, or the credit is not activated yet.",
}


def fail(step: str, exc: Exception) -> None:
    kind = classify_error(exc)
    print(f"  FAILED ({kind}): {HELP.get(kind, str(exc)[:200])}")
    print(f"\nStopped at: {step}.")
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chat", metavar="MODEL_ID", help="chat model id to test")
    args = parser.parse_args()
    get_settings()  # loads the .env files into the environment

    print("1. Settings")
    key = os.getenv("NEBIUS_API_KEY", "").strip()
    base_url = os.getenv("NEBIUS_BASE_URL", "").strip().rstrip("/")
    embedding_model = os.getenv("EMBEDDING_MODEL", "").strip()
    if not key or key.startswith("your-"):
        print("  NEBIUS_API_KEY is missing. Add it to the .env file in the project folder.")
        sys.exit(1)
    print(f"  NEBIUS_API_KEY   found ({len(key)} characters)")
    if not base_url:
        print("  NEBIUS_BASE_URL is missing. Add NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1")
        sys.exit(1)
    if base_url.endswith(("/embeddings", "/chat/completions")):
        print(f"  NEBIUS_BASE_URL ends with an operation ({base_url}). Remove everything after /v1.")
        sys.exit(1)
    print(f"  NEBIUS_BASE_URL  {base_url}")
    print(f"  EMBEDDING_MODEL  {embedding_model or '(not set yet)'}")

    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base_url, timeout=60, max_retries=1)

    print("\n2. Models your key can use")
    try:
        ids = sorted(m.id for m in client.models.list())
    except Exception as exc:  # noqa: BLE001 - shown to the user with advice
        fail("listing models", exc)
    embedding = [i for i in ids if any(h in i.lower() for h in EMBEDDING_HINTS)]
    chat = [i for i in ids if i not in embedding]
    print("  EMBEDDING models (for EMBEDDING_MODEL):")
    for model_id in embedding:
        print(f"    {model_id}")
    print("  CHAT models (for answers and the eval judge):")
    for model_id in chat:
        print(f"    {model_id}")

    print("\n3. Embedding test")
    if not embedding_model:
        print("  Skipped: set EMBEDDING_MODEL in .env to one of the EMBEDDING models above, then run this again.")
    else:
        try:
            started = time.monotonic()
            vector = client.embeddings.create(model=embedding_model, input="hello conference").data[0].embedding
            print(f"  OK: {embedding_model} returned {len(vector)} numbers (the dimension) "
                  f"in {time.monotonic() - started:.1f} s")
        except Exception as exc:  # noqa: BLE001
            fail("the embedding test", exc)

    print("\n4. Chat test")
    if not args.chat:
        print("  Skipped: run again with --chat MODEL_ID, using one of the CHAT models above.")
    else:
        try:
            started = time.monotonic()
            reply = client.chat.completions.create(
                model=args.chat, max_tokens=10,
                messages=[{"role": "user", "content": "Reply with the single word OK."}])
            text = (reply.choices[0].message.content or "").strip()
            print(f"  OK: {args.chat} replied {text!r} in {time.monotonic() - started:.1f} s")
        except Exception as exc:  # noqa: BLE001
            fail("the chat test", exc)

    print("\nDone.")


if __name__ == "__main__":
    main()
