"""Default spec confirmation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from data_intelligence_sdk.core.errors import SpecConfirmationRequired
from data_intelligence_sdk.core.types import ExecutionSpec, SessionContext, UserContext

SpecConfirmationAction = Literal["ok", "revise"]


@dataclass(slots=True)
class SpecConfirmationRequest:
    """Structured request an app/UI can present to a user."""

    spec: ExecutionSpec
    message: str


@dataclass(slots=True)
class SpecConfirmationDecision:
    """User decision produced by a confirmation provider."""

    action: SpecConfirmationAction
    feedback: str | None = None


class SpecConfirmationProvider(Protocol):
    """App-owned boundary for asking a user whether a spec is acceptable."""

    def request_confirmation(
        self, request: SpecConfirmationRequest
    ) -> SpecConfirmationDecision:
        """Return the user's confirmation decision."""


class DefaultSpecConfirmation:
    """Asks a provider to accept or revise a spec before execution."""

    def __init__(self, provider: SpecConfirmationProvider | None = None) -> None:
        self.provider = provider

    def confirm(
        self,
        spec: ExecutionSpec,
        session_context: SessionContext | None = None,
        user_context: UserContext | None = None,
    ) -> ExecutionSpec | SpecConfirmationDecision:
        del session_context, user_context
        if spec.confirmed:
            return spec

        request = self.build_request(spec)
        if self.provider is None:
            raise SpecConfirmationRequired(
                "Execution spec requires user confirmation.",
                request=request,
            )

        decision = self.provider.request_confirmation(request)
        if decision.action == "ok":
            spec.confirmed = True
            return spec
        if decision.action == "revise":
            return decision
        raise ValueError(f"Unsupported spec confirmation action: {decision.action}")

    def build_request(self, spec: ExecutionSpec) -> SpecConfirmationRequest:
        capability_names = [
            requirement.name for requirement in spec.capability_requirements
        ]
        message = (
            "Confirm execution spec before running an engine.\n"
            f"Objective: {spec.objective}\n"
            f"Intent: {spec.intent}\n"
            f"Data requirements: {spec.data_requirements}\n"
            f"Capabilities: {capability_names}\n"
            f"Constraints: {spec.constraints}\n"
            f"Engine hint: {spec.engine_hint}"
        )
        return SpecConfirmationRequest(spec=spec, message=message)


class StaticSpecConfirmationProvider:
    """Deterministic provider for tests and non-interactive examples."""

    def __init__(self, decisions: list[SpecConfirmationDecision]) -> None:
        self._decisions = list(decisions)

    def request_confirmation(
        self, request: SpecConfirmationRequest
    ) -> SpecConfirmationDecision:
        del request
        if not self._decisions:
            raise SpecConfirmationRequired("No static spec confirmation decisions remain.")
        return self._decisions.pop(0)


class ConsoleSpecConfirmationProvider:
    """Terminal-backed provider for local interactive demos."""

    def __init__(
        self,
        *,
        input_func: Callable[[str], str] = input,
        output_func: Callable[[str], None] = print,
    ) -> None:
        self.input_func = input_func
        self.output_func = output_func

    def request_confirmation(
        self, request: SpecConfirmationRequest
    ) -> SpecConfirmationDecision:
        self.output_func("")
        self.output_func("=== Draft Execution Spec ===")
        self.output_func(request.message)
        self.output_func("")

        while True:
            choice = self.input_func("Choose [ok/revise]: ").strip().lower()
            if choice in {"ok", "o", "yes", "y"}:
                return SpecConfirmationDecision(action="ok")
            if choice in {"revise", "r", "edit", "e"}:
                feedback = self.input_func("Revision feedback: ").strip()
                if feedback:
                    return SpecConfirmationDecision(
                        action="revise",
                        feedback=feedback,
                    )
                self.output_func("Revision feedback cannot be empty.")
                continue
            self.output_func("Please type 'ok' or 'revise'.")
