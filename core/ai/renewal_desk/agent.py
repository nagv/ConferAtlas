"""Assembles the Sponsor Renewal Desk: a Deep Agent orchestrator with four specialist subagents.

- Planning: the orchestrator's ``write_todos`` list (one item per sponsor) is the run's progress.
- Delegation: ``task`` hands one step for one sponsor to an isolated specialist.
- Files: a per-run virtual filesystem in the agent state (``StateBackend``), so notes and
  recommendations survive with the checkpoint. ``/skills/`` is the repo's skills folder, read-only.
  ``/memories/`` is a store shared across runs (past outcomes and feedback per sponsor).
- Human approval: ``finalize_pack`` is in ``interrupt_on``, so the graph pauses before every save
  and resumes with the person's decision (approve, edit or reject with feedback).
"""
from __future__ import annotations

from core.ai.renewal_desk.subagents import ORCHESTRATOR_PROMPT, SPECIALISTS, TOOL_GROUP
from core.ai.renewal_desk.tools import RenewalWorkspace
from core.ai.skills import SKILLS_DIR

RECURSION_LIMIT = 400        # graph steps per invoke (not model calls); a full 5-sponsor run needs about 250
APPROVAL_TOOL = "finalize_pack"
SPECIALIST_MODEL_CALLS = 12  # per delegated task: stops a specialist that wanders (e.g. grep after grep)
ORCHESTRATOR_MODEL_CALLS = 60  # per start or resume
MODEL_RETRIES = 3            # per model call, with exponential backoff, for network and rate-limit errors


def _guards(model_calls: int) -> list:
    from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware

    return [ModelRetryMiddleware(max_retries=MODEL_RETRIES, on_failure="error"),
            ModelCallLimitMiddleware(run_limit=model_calls, exit_behavior="end")]


def _backend(store=None):
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend

    routes = {"/skills/": FilesystemBackend(root_dir=str(SKILLS_DIR), virtual_mode=True)}
    if store is not None:
        routes["/memories/"] = StoreBackend(namespace=lambda _runtime: ("renewal_desk",), store=store)
    return CompositeBackend(default=StateBackend(), routes=routes)


def _permissions():
    from deepagents import FilesystemPermission

    return [
        FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
        FilesystemPermission(operations=["read", "write"], paths=["/sponsors/**", "/memories/**"]),
        FilesystemPermission(operations=["read"], paths=["/skills/**"]),
    ]


def build_renewal_agent(workspace: RenewalWorkspace, model, checkpointer=None, store=None):
    """The compiled graph. ``model`` is a LangChain chat model with tool calling."""
    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware

    groups = workspace.tools()
    subagents = [
        {"name": name, "description": description, "system_prompt": prompt, "tools": groups[TOOL_GROUP[name]],
         "middleware": _guards(SPECIALIST_MODEL_CALLS), **({"skills": ["/skills/"]} if name == "writer" else {})}
        for name, (description, prompt) in SPECIALISTS.items()
    ]
    return create_deep_agent(
        model=model,
        tools=groups["orchestrator"],
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=subagents,
        # write_todos is not added by default in deepagents 0.7
        middleware=[TodoListMiddleware(), *_guards(ORCHESTRATOR_MODEL_CALLS)],
        backend=_backend(store),
        permissions=_permissions(),
        interrupt_on={APPROVAL_TOOL: {"allowed_decisions": ["approve", "edit", "reject"]}},
        checkpointer=checkpointer,
        store=store,
        name="renewal_desk",
    )
