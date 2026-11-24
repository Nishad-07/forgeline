"""Agent base class.

An agent = a role with an explicit contract:
  * inputs        -> keyword arguments to `run`
  * output        -> a Pydantic schema (`schema`)
  * responsibility-> its system prompt
  * checks        -> semantic validation beyond the schema (e.g. "files to modify must exist")

Two layers of output validation:
  1. Schema validation and repair happen in the LLM router (malformed JSON, missing fields).
  2. Semantic checks happen here. Failed checks are fed back to the model as concrete problems, up to
     policy.autonomy.max_repair_attempts times, then AgentOutputError is raised and the workflow
     escalates instead of continuing with unsupported output.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from forgeline.llm.backends import Message
from forgeline.llm.router import LLMRouter
from forgeline.policy import Policy

T = TypeVar("T", bound=BaseModel)

SEMANTIC_REPAIR = (
    "Your answer cannot be accepted:\n{problems}\n\n"
    "Fix every problem listed and reply again with your complete corrected answer, in the required format."
)


class AgentOutputError(Exception):
    def __init__(self, agent: str, problems: list[str]) -> None:
        super().__init__(f"{agent} output rejected: " + "; ".join(problems))
        self.agent = agent
        self.problems = problems


class Agent(Generic[T]):
    name: ClassVar[str] = "agent"
    tier: ClassVar[str] = "light"
    schema: ClassVar[type[BaseModel]]
    system_prompt: ClassVar[str] = ""

    def __init__(self, router: LLMRouter, policy: Policy) -> None:
        self.router = router
        self.policy = policy

    # Subclasses implement these two.
    def build_prompt(self, **inputs: Any) -> str:
        raise NotImplementedError

    def check(self, result: T, **inputs: Any) -> list[str]:
        return []

    # Plain-text protocol (QA, Developer): the model answers in text and the agent parses it into
    # `schema`. Subclasses set protocol = "text" and implement `parse`, raising ValueError on bad format.
    protocol: ClassVar[str] = "json"

    def parse(self, text: str) -> T:
        raise NotImplementedError

    def _ask(self, messages: list[Message], run_id: str | None, temperature: float | None,
             order: list[str]) -> tuple[T | None, str, list[str]]:
        """One model call. Returns (result or None, raw text for the repair prompt, problems)."""
        if self.protocol == "text":
            text = self.router.text(messages, tier=self.tier, agent=self.name, run_id=run_id,
                                    temperature=temperature, order=order)
            try:
                return self.parse(text), text, []
            except ValueError as exc:
                return None, text, [f"output format: {exc}"]
        result = self.router.structured(self.schema, messages, tier=self.tier, agent=self.name, run_id=run_id,
                                        temperature=temperature, order=order)
        return result, result.model_dump_json(), []  # type: ignore[return-value]

    def run(self, run_id: str | None = None, **inputs: Any) -> T:
        """Call the model, check the result, repair; escalate to the next provider if repairs run out.

        Repair retries use a non-zero temperature: at temperature 0 a local model tends to return the
        identical wrong answer even after being told exactly what is wrong.
        """
        initial: list[Message] = [("system", self.system_prompt), ("human", self.build_prompt(**inputs))]
        order = self.router.provider_order()
        problems: list[str] = []
        for round_index in range(len(order)):
            providers = order[round_index:]
            if round_index:
                self.router.emit(run_id, "agent_escalation", self.name, {
                    "from": order[round_index - 1], "to": providers[0], "problems": problems[:5],
                })
            messages = list(initial)  # fresh start: don't show a stronger model the weaker one's mistakes
            for attempt in range(self.policy.autonomy.max_repair_attempts + 1):
                result, raw_text, problems = self._ask(
                    messages, run_id, None if attempt == 0 else 0.4, providers
                )
                if result is not None:
                    problems = self.check(result, **inputs)
                    if not problems:
                        self.router.emit(run_id, "agent_output", self.name,
                                         {"agent": self.name, "output": result.model_dump()})
                        return result
                self.router.emit(run_id, "agent_output_rejected", self.name,
                                 {"attempt": attempt + 1, "problems": problems[:10]})
                messages = [
                    *messages,
                    ("ai", raw_text[:12000] or "(empty)"),
                    ("human", SEMANTIC_REPAIR.format(problems="\n".join(f"- {p}" for p in problems))),
                ]
        raise AgentOutputError(self.name, problems)
