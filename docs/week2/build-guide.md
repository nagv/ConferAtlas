# Week 2 build guide: RAG for ConferAtlas

Step-by-step instructions for adding retrieval-augmented generation (RAG) to ConferAtlas, with the reason behind each step. The prompts that drive each step are in [`build-prompts.md`](build-prompts.md).

**Deadline:** Sunday, October 18, 2026, 11:59 PM Pacific. **Branch:** `week2-rag`.

## Contents
- [What we are building](#what-we-are-building)
- [Schedule](#schedule)
- [Step 0: External setup (you do these)](#step-0-external-setup-you-do-these)
- [Steps 1 to 11: The build](#steps-1-to-11-the-build)
- [Submission checklist](#submission-checklist)

---

## What we are building

Week 1 answers numeric questions from CSVs. Week 2 adds the documents behind those numbers: post-event reports, pricing brochures, sponsor decks, operations SOPs and session abstracts. The agent can then answer "why" and "what did we promise" questions, with citations.

```
Documents (PDF, DOCX, PPTX, MD)
   │  Step 3: load + clean  →  shared Document schema
   ▼
Chunks  ←  Step 4: fixed / structure-aware / semantic (+ contextual headers)
   │  Step 5: embed (Nebius) + store (Chroma, one collection per corpus)
   ▼
Retriever  ←  Step 6: dense + BM25 → hybrid → re-rank → refusal threshold
   │
   ▼
Agent (Step 7)  — numeric tools (Week 1)  +  one search tool per corpus
   │  routes each sub-question to the right tool, cites sources, refuses when nothing is found
   ▼
Ask page (Step 8) · RAG Lab (Step 9) · LangSmith evals (Step 10)
```

**Why this design:**
- **Separate collections per corpus** let the agent choose where to look (routing), and keep citations clean. This is the Agentic RAG pattern from the course.
- **Hybrid search** finds exact names (sponsors, venues) through BM25 and meaning through embeddings. Re-ranking then puts the best few first.
- **The refusal path comes first:** if nothing relevant is found, the app says so instead of guessing.
- **The analysis layer still owns every number.** RAG adds narrative evidence, never new calculations.

## Schedule

| Day | Steps | Result |
|---|---|---|
| Friday 16 | 0 to 5 | Accounts ready, corpus generated, documents loaded, chunked and indexed |
| Saturday 17 | 6 to 9 | Hybrid retrieval, agentic RAG in the Ask page, RAG Lab |
| Sunday 18 | 10 to 11 | Evaluation, tests, README, deploy, demo video and submission doc |

If time runs short, drop in this order: the embedding map, semantic chunking, Graph RAG (stretch).

---

## Step 0: External setup (you do these)

None of these keys go into the code or the frontend. They all live in `.env` in the project folder (or `backend/.env`, which the backend reads first). Git ignores both. For Render, add them as **Environment Variables** (not Secret Files; see prompt 45 in the log).

### 0.1 Pinecone: sign up today (optional, deadline Oct 16, 5 PM Pacific)
We use Chroma for Week 2, but the cohort promotion needs sign-up by today.
1. Follow the Pinecone guide on the cohort credits page (tga-cohort-credits-hub.vercel.app, Pinecone → View instructions).
2. Sign up with the email you used on the cohort intake form.
3. Stay on the **Starter** plan until the promotion is confirmed by email. Don't upgrade or add a card.

### 0.2 Nebius: embeddings and generation
**Why Nebius:** the Gemini free tier allows only a few requests per minute. Building the index and running the evaluation make hundreds of calls, so we use Nebius, which the cohort credits pay for.

**What you get from Nebius:** two models, used for different jobs.

| Model | Job in ConferAtlas | Where it goes in `.env` |
|---|---|---|
| **Embedding model** | Turns each chunk of text, and each question, into a list of numbers (a vector), so similar meanings can be found. Used when building the index and on every search. | `EMBEDDING_MODEL` |
| **Chat model** | Writes the answers, and later grades them as the eval judge. | `LLM_MODEL` (with `LLM_PROVIDER=nebius`) |

**Where the settings live:** the backend reads `backend/.env` first, then `.env` in the project folder. You have only the project-folder `.env` today, so add everything below to that file. It's ignored by Git, so your key is never committed.

#### A. Get access and a key
1. Open the cohort credits page (tga-cohort-credits-hub.vercel.app), choose **Nebius → View instructions**, and follow the application or activation steps. Your credit must be active before calls work.
2. Sign in to **Nebius Token Factory** (tokenfactory.nebius.com) with the same email.
3. Go to **API keys → Create API key**. Name it `conferatlas-dev`.
4. Copy the key immediately; it's shown only once. Don't paste it into chats, screenshots or commands.

#### B. Add the key and base URL to `.env`
5. Open `.env` in the project folder (`C:\Users\rajvo\Gen Academy\ConferAtlas\.env`) in a text editor.
6. Add these lines at the end. Replace only the part after the first `=`, with no quotes or spaces:
   ```
   # --- Nebius ---
   NEBIUS_API_KEY=paste-your-key-here
   NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1
   ```
   **The base URL** is the address of the Nebius API. The code adds the operation to it (`/embeddings` or `/chat/completions`), so it must stop at `/v1`. Nebius speaks the same request format as OpenAI, which is why the app can use the standard OpenAI client with this address.
7. Save the file.

#### C. See which models your key can use
8. Open a terminal in the project folder and run:
   ```bash
   .venv\Scripts\python.exe scripts\check_nebius.py
   ```
9. It checks the key and base URL, then prints two lists: **EMBEDDING models** and **CHAT models**. If it stops with an error, the message says what to fix (for example "The key was rejected").

#### D. Choose the two models
10. **Embedding model:** pick one from the EMBEDDING list. Prefer, in this order:
    1. A **Qwen3 embedding** model (for example one whose ID contains `Qwen3-Embedding`): strong on English business text.
    2. A **BGE** model (ID contains `bge`): a solid, widely used alternative.

    Either is fine for 36 documents. Avoid "multilingual" variants unless you need other languages; they're bigger for no gain here.
11. **Chat model:** pick one from the CHAT list that is:
    - an **Instruct** model (the ID usually contains `Instruct`), so it follows instructions and supports tool calling, which the Ask agent needs;
    - **mid-size**, roughly 8B to 70B parameters (the number in the ID, such as `70B`): fast and cheap enough for the 20-question evaluation;
    - **not** a "thinking" or reasoning model (IDs with `R1`, `thinking` or `reasoning`): those are slower and use far more tokens.

    A Llama 3.x 70B Instruct or a Qwen Instruct model is a good default.
12. Copy both IDs **exactly** as printed, including the part before the `/`.

#### E. Save the embedding model and test both
13. Add to `.env`:
    ```
    EMBEDDING_PROVIDER=nebius
    EMBEDDING_MODEL=paste-the-embedding-model-id
    ```
14. Run the checker again, with your chat model ID:
    ```bash
    .venv\Scripts\python.exe scripts\check_nebius.py --chat paste-the-chat-model-id
    ```
15. Success looks like:
    ```
    3. Embedding test
      OK: <model> returned 4096 numbers (the dimension) in 0.6 s
    4. Chat test
      OK: <model> replied 'OK' in 1.2 s
    ```
    Write down the **dimension** (the number of numbers in each vector) for the submission doc. Bigger vectors take more space and are slightly slower; at this corpus size either is fine.

#### F. Decide what answers the questions
16. For now, **keep your current chat settings** (`LLM_PROVIDER` and `LLM_MODEL`) so Week 1 keeps working exactly as it does. Step 1 already lets you switch with two lines when you're ready:
    ```
    LLM_PROVIDER=nebius
    LLM_MODEL=paste-the-chat-model-id
    ```
    We'll switch during Step 7, when the agent starts using documents.
17. **On Render**, add the same variables (`NEBIUS_API_KEY`, `NEBIUS_BASE_URL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`) under **Environment → Environment Variables**, not as Secret Files. We do this in Step 11.

**If Nebius isn't approved in time:** use Google instead with `EMBEDDING_PROVIDER=google_genai` and `EMBEDDING_MODEL=gemini-embedding-2` (your `GOOGLE_API_KEY` is reused). It works, but the free tier's limits make indexing and the evaluation slower.

### 0.3 LangSmith: tracing and evaluation
**Why:** LangSmith records every agent step (plan, tool calls, retrieved chunks, answer) and compares eval runs side by side. That gives you the comparison report and demo footage without extra code.

1. Claim the $100 credit using the instructions in the Nebius bundle email.
2. Sign in at smith.langchain.com.
3. **Settings → API Keys → Create API Key.** Create two keys, each with only the access it needs:
   - **Local key: Personal Access Token**, described as `conferatlas-local`. Your laptop both sends traces and runs the evaluation, which creates datasets and experiments, so it needs full access. A Tracing-only key can't run the evaluation.
   - **Render key: Tracing only**, described as `conferatlas-render`, created in Step 11. The deployed backend only sends traces, so if this key ever leaked, it couldn't read or delete your data.
   - A **Service key** is for shared or team systems that shouldn't depend on one person's account. Not needed for a solo project.
4. If your account is in the EU region, your endpoint is `https://eu.api.smith.langchain.com`; otherwise leave the endpoint out.
5. Add to `.env` in the project folder:
   ```
   LANGSMITH_TRACING=true
   LANGSMITH_API_KEY=...
   LANGSMITH_PROJECT=conferatlas
   ```
6. After Step 7, ask the agent a question, then open LangSmith → **Projects → conferatlas** to see the trace.

**Privacy:** traces contain the prompts and retrieved text. Keep tracing on only for the synthetic demo data. Turn it off (`LANGSMITH_TRACING=false`) before anyone uploads real data.

### 0.4 Chroma: the vector database (nothing to sign up for)
**What it is:** Chroma is an open-source vector database that runs inside the backend process and saves to a folder. There is no server or account.

- **Install:** Step 1 adds `chromadb` and `langchain-chroma` to `backend/requirements.txt`. Install with:
  ```bash
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt
  ```
- **Where the data lives:** `data/index/chroma/` (Git ignores it). Deleting the folder resets the index; Step 5's build script recreates it.
- **Collections:** one per corpus and chunking setting, for example `event_reports__structure_400`. A collection holds each chunk's text, its embedding vector and its metadata.
- **Look inside it yourself** after Step 5:
  ```bash
  .venv\Scripts\python.exe -c "import chromadb; c = chromadb.PersistentClient(path='data/index/chroma'); [print(col.name, col.count()) for col in c.list_collections()]"
  ```
  Then peek at a few chunks:
  ```bash
  .venv\Scripts\python.exe -c "import chromadb; c = chromadb.PersistentClient(path='data/index/chroma'); col = c.list_collections()[0]; print(col.peek(3)['documents'])"
  ```
  The RAG Lab page (Step 9) is the visual version of this.
- **On Render:** the disk is wiped at each deploy, so the backend rebuilds the index at startup when it's missing (Step 11). The corpus is small, so this takes about a minute. Pinecone is the managed alternative if you ever need the index to persist.

### 0.5 Fireworks AI: backup provider (optional, redeem by Oct 21)
1. Redeem the $15 code from your email on fireworks.ai under **Billing**.
2. Create an API key under **API Keys**.
3. It's OpenAI-compatible at `https://api.fireworks.ai/inference/v1`. Use it only if Nebius isn't available; the settings from Step 1 accept any OpenAI-compatible base URL.

---

## Steps 1 to 11: The build

Each step lists what changes, why, and how to check it yourself.

### Step 1: Settings and dependencies (prompt 1)
- **Changes:** new packages in `backend/requirements.txt`; embedding, Nebius and LangSmith settings in `core/config.py`; `backend/.env.example` updated.
- **Why:** every later step reads these settings, and keys stay in the backend only.
- **Check:** `pytest` still passes, and the app runs as before with no new keys.

### Step 2: Synthetic document corpus (prompt 2)
- **Changes:** `core/docgen.py` writes `data/synthetic/docs/` (36 files in 5 corpora: 12 post-event report PDFs, 2 pricing brochure PDFs, 6 sponsor decks, 4 SOPs, 12 abstract files, plus `manifest.json`) and `data/synthetic/eval/golden_set.json` (20 questions). Run it with `.venv\Scripts\python.exe scripts\generate_data.py`.
- **Why:** RAG needs documents. Building them from the same seed as the CSVs keeps every figure consistent, so an answer that mixes a CSV number and a report quote never contradicts itself. Facts that exist only in the documents force the app to retrieve.
- **Check:** open a post-event report PDF and the pricing brochure; check the numbers against the Event drill-down page.

### Step 3: Loaders and the Document schema (prompt 3)
- **Changes:** `core/rag/loaders.py`, `core/rag/schema.py`.
- **Why:** every format becomes the same `Document` shape, so chunking, indexing and citations don't care where the text came from. Metadata (page, slide, section, event) is what makes citations and filters possible.
- **Check:** the loader tests pass; a PDF table comes out as a Markdown table, not a jumble of numbers. To see what the loaders produce for one document, run:
  ```bash
  .venv\Scripts\python.exe -c "from core.rag.loaders import load_corpus; [print(d.metadata, d.page_content, sep='\n', end='\n\n') for d in load_corpus('data/synthetic') if d.metadata['doc_id'] == 'report_EV008']"
  ```
  Change `report_EV008` to `pricing_2026`, `sop_booth_logistics`, `deck_SP016` or `abstracts_EV001` to see the other formats.

### Step 4: Chunking (prompt 4)
- **Changes:** `core/rag/chunking.py`.
- **Why:** chunking decides what the retriever can find. Fixed chunks are simple but cut sentences and tables in half. Structure-aware chunks follow headings, pages and slides. Semantic chunks split where the topic changes. The contextual header puts the event and document name in every chunk, so a chunk that says "the venue" still matches a question about Boston.
- **Result on our corpus:** fixed 500/50 gives 61 chunks (median 374 tokens) and cuts through tables; structure-aware 400 gives 250 chunks, one per section (median 85 tokens), with tables whole and page breaks mended.
- **Check:** in RAG Lab (Step 9) you'll see the boundaries. Before then, compare one document's chunks in the two strategies:
  ```bash
  .venv\Scripts\python.exe -c "from core.rag.loaders import load_corpus; from core.rag.chunking import PRESETS, chunk_corpus; units = load_corpus('data/synthetic'); [print(c.metadata['chunk_id'], c.metadata['tokens'], 'tokens', c.page_content, sep='\n', end='\n\n') for name in ('fixed', 'structure') for c in chunk_corpus(units, PRESETS[name]) if c.metadata['doc_id'] == 'report_EV008']"
  ```

### Step 5: Embeddings and the index (prompt 5)
- **Changes:** `core/rag/embeddings.py` (the model, query instructions, a disk cache of vectors), `core/rag/index.py` (Chroma collections and incremental builds), `scripts/build_index.py`.
- **Why:** embeddings turn each chunk into a vector so similar meanings are close together. Hashing each document means a rebuild re-embeds only what changed, and the vector cache means the same text is never paid for twice.
- **Result on our corpus:** 15 collections (5 corpora x 3 chunking settings); the first build sent 1,684 texts to Qwen3-Embedding-8B in about 3.5 minutes; rebuilds take seconds. Embedding latency on Nebius varies from 0.2 s to about 7 s per call (cold connections are slowest), so the app keeps one client warm and the evaluation measures latency as it really is.
- **Check:**
  ```bash
  .venv\Scripts\python.exe scripts\build_index.py --strategy all
  .venv\Scripts\python.exe scripts\build_index.py --status
  ```
  Run the first command twice: the second run shows every document as "same" and 0 texts sent to the model. Then try the Chroma commands from Step 0.4 (the index is in `data\index\chroma`).
- **See the vectors:** draw the index as a 2D map (one point per chunk, by corpus), with a question placed on it and its five nearest chunks ringed:
  ```bash
  .venv\Scripts\python.exe scripts\plot_embeddings.py --question "Which sponsor asked for attendee job titles in the lead export?"
  ```
  Open `data\index\embedding_map.html` in a browser and hover the points. The map squeezes 4096 numbers into 2, so it's a sketch: search uses the real distances, which the script prints.

### Step 6: Retrieval (prompt 6)
- **Changes:** `core/rag/retrieve.py`.
- **Why:** dense search misses exact names; BM25 misses meaning. Hybrid search combines them, and a cross-encoder re-ranker reads each question and chunk together to pick the best few. The threshold is the "I don't know" path.
- **Check:** search "Which sponsor asked for a bigger booth?" with and without BM25 and compare.
- **Result:** on the 17 answerable golden questions, the right document came first for dense 17/17, BM25 14/17, hybrid 16/17 and hybrid with re-ranking 17/17; off-topic questions were refused 3 of 3 with re-ranking. For "which sponsor asked for attendee job titles", the answer chunk moved from third (dense) to first (hybrid, re-ranked). Two findings: separate BM25 indexes per corpus made hybrid worse (scores weren't comparable), so one shared index is used; and a question about a real event whose answer isn't written down (the hotel rate) still re-ranks at 0.99, so refusals also need the answer step (Step 7).

### Step 7: Agentic RAG (prompt 7)
- **Changes:** new search tools in `core/ai/portfolio_agent.py`, citation and refusal rules.
- **Why:** one agent with well-described tools can route a simple question to one corpus, and split a compound question into parts (decomposition). The week 1 rule stays: numbers come from the numeric tools.
- **Check:** ask a mixed question; the trace should show a numeric tool call and a document search, and the answer should cite both.
- **Result:** with Qwen3-30B-A3B on Nebius, the mixed question calls `event_kpis` (80% show-up rate, [T1]) and `search_event_reports` (the reminder-email recommendation, [S2]); compound questions get one tool call per part. Three fixes came from live answers: the model sometimes answered without any tool (now it gets one reminder to use the tools, so off-topic questions are refused), it took a sponsor's package from the price list (now facts about a sponsor must come from a result about that sponsor), and it padded answers with bullets the sources don't state (now bullets only for stated facts).

### Step 8: Backend API and the Ask page (prompt 8)
- **Changes:** new endpoints in `backend/`, citations and a trace on the Ask page.
- **Why:** the frontend stays presentation-only; the backend owns the index, the keys and the AI.
- **Check:** `/docs` lists the new endpoints; `tests/test_architecture.py` passes.
- **Result:** the chat answer now carries `sources` and `trace`; new endpoints `/rag/documents`, `/rag/chunks`, `/rag/map`, `/rag/build`, `/rag/experiments` and `/datasets/{id}/rag/search`; `/status` reports whether document search is ready. Document search is off for uploaded datasets (the documents describe the demo events). The Ask page shows "Sources (n cited, m retrieved)" and "How this was answered" under each answer, plus document starter questions.

### Step 9: RAG Lab (prompt 9)
- **Changes:** a new RAG Lab page.
- **Why:** you can see what the retriever sees, change one setting and watch the result change. This is also the core of the demo video.
- **Check:** change the chunk size and watch the histogram and the retrieved chunks change.
- **Result:** four tabs: Chunks (two settings side by side, chunk cards, corpus histogram; works without the embedding model except for semantic), Search (tuning panel, score chart with the refusal line, candidates table with rank movement, and a "Build this setting now" button for an unindexed chunk size), Embedding map, and Experiments (filled by Step 10).

### Step 10: Evaluation (prompt 10)
- **Changes:** `scripts/run_eval.py`, results in `docs/week2/`.
- **Why:** the guideline asks for measured faithfulness and relevance and a chunking and re-ranking comparison. The golden set includes unanswerable questions to test the refusal path.
- **Metrics:**
  - **Hit rate:** did any of the expected sources appear in the top k?
  - **MRR:** how high did the first correct source rank?
  - **Faithfulness:** is every claim supported by the retrieved text (LLM judge)?
  - **Answer relevance:** does the answer address the question (LLM judge)?
  - **Refusal accuracy:** did the app refuse exactly the unanswerable questions?
  - **Routing accuracy:** did the agent search the expected corpus?
  - **Latency:** seconds per answer, against the 8-second target.
- **Check:** LangSmith → **Datasets & Experiments** shows one experiment per setting, side by side.
- **Run it:**
  ```bash
  .venv\Scripts\python.exe scripts\run_eval.py
  ```
  Add `--questions 3` for a quick smoke run, `--retrieval-only` for no LLM calls, or `--agent fixed --merge` to rerun one setting. Answers use `deepseek-ai/DeepSeek-V4-Flash-0731` and the judge `zai-org/GLM-5.3` (changed from the Qwen models on October 18, with approval, because Nebius's Qwen models took 5 to 35 s per call that day). Both are billed from the Nebius credit; the full run costs roughly $1 to $2.
- **Result (full table in [`eval_results.md`](eval_results.md)):** the production setting (structure-aware, hybrid, re-ranked) scored hit rate 100%, MRR 1.00, routing 100%, faithfulness 100%, relevance 100%, correctness 99%, refusal accuracy 95%, with 95% of answers under 8 s (median 2.5 s). Fixed 500-token chunks were clearly worst end to end: refusal 70%, relevance and correctness 81%, median 6.1 s.
- **Findings:**
  - Re-ranking lifts MRR from 0.96 (hybrid) to 1.00 for structure-aware and semantic chunks, at about 0.9 s per search on the CPU.
  - Fixed chunks lose end to end because each source shown to the model is capped at 1,200 characters: a 500-token chunk (about 2,000 characters) was cut before the answer, and the agent honestly refused. Chunk size and the context given per source have to fit together.
  - Retrieval alone refuses only off-topic questions (85 to 90% refusal accuracy); refusing the unanswerable questions about real events takes the agent's answerability check (95%).
  - Q20 ("the dates of next year's events") turned out ambiguous: the data includes 2026 events, which the agent listed with citations. It counts as a miss, but the golden question should be reworded.
  - Evaluating the evaluator: the reasoning judge sometimes returned no grade (its thinking used up the output budget), first leaving 6 to 10 of 20 answers ungraded per setting. With a larger budget, a JSON fallback and one retry, 16 to 19 of 20 are graded, and the "Judged" column shows the count so a missing grade can't hide in an average.
  - One answer stalled for 54 minutes on a hung request; each evaluated answer now has a 3-minute limit.

### Step 11: Tests, docs and deployment (prompt 11)
- **Changes:** tests, README, startup index build.
- **Check:** the full test suite passes, and the Render app answers a document question.

---

## Submission checklist
- [ ] **Google Doc:** overview and one-liner, the framework table, corpus description, prompts (from `build-prompts.md`), iterations tried (with the eval tables), learnings.
- [ ] **Video (5 minutes or less):** the problem, a document question with citations, a mixed question with the trace, an unanswerable question, RAG Lab (change a setting, see the effect), the eval comparison, and how AI coding tools were used.
- [ ] **GitHub:** `week2-rag` merged to `main` and pushed.
- [ ] **Form:** submitted at the link in the Week 2 handout before Sunday 11:59 PM Pacific.
