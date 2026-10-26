# Week 2 prompts

The prompts used to build ConferAtlas Week 2 (Agentic RAG over the conference documents), word for word and in order, grouped by step. Prompts asked only to learn or clarify a concept are left out; the full log with answers is in [`docs/prompts.md`](../prompts.md) (entry numbers in brackets).

## Planning and scope

1. > https://docs.google.com/document/d/1J86kVvwlSb8EJH8mOpZE6t4XSXzS58y0ukMvmbtmkn8/edit?tab=t.0 can you read this. this is project 2 (week 2) submission guideline

   (#96)

2. > with the proposal for sunday, can i graphically see the chunks and fine tune etc?

   (#97)

3. > We were introduced Agentic RAG and Graph RAG part of the course
   >
   > https://github.com/The-Gen-Academy/Mastering-Agentic-AI-Week2-Session2
   >
   > do you think these concepts give value add to the week 2

   (#98)

4. > I have access to some of these tools part of course.. any of these things expediate or make things prettier
   >
   > https://tga-cohort-credits-hub.vercel.app/#tools

   (#99)

5. > yes.. create a week 2 build prompts and also keep storing the chats against in to repo as we progress. Also, give me step by step instructions for building. Also, explain me each step while you are building.
   > Where ever setup required in external system or Chroma, give me detailed instructions so that I can try my self

   (#100)

## Step 0: Nebius setup

6. > its confusing... give me full instructions of setting up nebius. start from model selection etc per plan. dont assume anything is already available

   (#104)

7. > step 8 return this
   >
   > *(pasted the checker's model list: one embedding model, Qwen/Qwen3-Embedding-8B, and 27 chat models)*

   (#105)

8. > this is the reply
   >
   > *(pasted the checker output: key and base URL found, EMBEDDING_MODEL Qwen/Qwen3-Embedding-8B, embedding test OK with 4096 dimensions in 0.3 s, chat test OK with Qwen/Qwen3-30B-A3B-Instruct-2507 in 0.3 s)*

   (#108)

9. > Test the judge model response
   >
   > *(pasted the checker output: embedding test OK in 0.2 s; chat test OK with Qwen/Qwen3-235B-A22B-Instruct-2507 in 0.5 s)*

   (#109)

## Step 2: Documents and golden set

10. > yes..before that summarize what's step 1.. is it setting up nebius and embeding model from the same. is this correct?

    (#115)

## Step 3: Loaders

11. > yes..start with step 3

    (#124)

## Step 4: Chunking

12. > start with Step 4

    (#125)

## Step 5: Embeddings and index

13. > yes proceed with Step 5

    (#126)

14. > can i see vectors created in UI

    (#127)

## Steps 6 to 9: Retrieval, Agentic RAG, API and RAG Lab

15. > can you implement today's tasks

    (#140)

## Step 10: Evaluation

16. > proceed with Step 10

    (#141)

17. > *(answer to "Which models should Step 10 use?")* is the suggested fast model is free or its comes part of Nebius's credit (I have $25 credit in Nebius)
    > if yes, proceed with recommendation

    (#142)

## Step 11: README

18. > only do this in Step 11
    > README: add the Week 2 features, setup (Nebius, LangSmith, Chroma), how to build the index and run the evaluation, and the results.

    (#145)

## Submission doc

19. > Draft a good doc for submission

    (#149)

## BM25 tokenizer fix

20. > fix this flaw

    (#157; the flaw: BM25 kept punctuation on words, so "2024:" in a heading never matched "2024" in a question)

21. > go with option 1

    (#175; option 1: keep the tokenizer fix and update the evaluation numbers in the README and the Google Doc)

## GitHub

22. > commit and push the changes to branch

    (#181)
