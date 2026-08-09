"""Execution contracts shared by the report planner, router, and runtime."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


METHOD_HUB_ROUTE = "existing_tool"
GENERATED_CODE_ROUTE = "generate_tool"
SEMANTIC_ANALYSIS_ROUTE = "semantic_analysis"
UNSUPPORTED_ROUTE = "unsupported"

ROUTE_KINDS = {
    METHOD_HUB_ROUTE,
    GENERATED_CODE_ROUTE,
    SEMANTIC_ANALYSIS_ROUTE,
    UNSUPPORTED_ROUTE,
}

EXECUTION_MODES = {
    "auto",
    "method_hub",
    "generated_code",
    SEMANTIC_ANALYSIS_ROUTE,
}

AUTO_EXECUTION_CLASS = "auto"
SOURCE_OPERATION_CLASS = "source_operation"
DETERMINISTIC_TRANSFORM_CLASS = "deterministic_transform"
SEMANTIC_INFERENCE_CLASS = "semantic_inference"

EXECUTION_CLASSES = {
    AUTO_EXECUTION_CLASS,
    SOURCE_OPERATION_CLASS,
    DETERMINISTIC_TRANSFORM_CLASS,
    SEMANTIC_INFERENCE_CLASS,
}

# Exact capability identifiers are part of the PlanStep protocol. This registry
# does not infer routing from prose, filenames, fields, domains, or user terms.
CAPABILITY_EXECUTION_CLASSES = {
    "semantic_analysis": SEMANTIC_INFERENCE_CLASS,
    "semantic_extraction": SEMANTIC_INFERENCE_CLASS,
    "semantic_content_extraction": SEMANTIC_INFERENCE_CLASS,
}


def execution_class_for_capability(operation: dict[str, Any]) -> str | None:
    capability = str(operation.get("capability") or "").strip().casefold()
    return CAPABILITY_EXECUTION_CLASSES.get(capability)

ARGUMENT_ADAPTERS = {
    "identity",
    "artifact_path",
    "records_to_text",
}

_STEP_OUTPUT_REF = re.compile(r"^step-output://([^/]+)/([^/]+)$")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


def execution_class_for_step(step: dict[str, Any]) -> str:
    """Return the normalized execution class declared by a PlanStep.

    The class is a routing contract, not an inference from English keywords,
    filenames, output field names, or a concrete tool. Older plans remain
    readable because an explicit execution mode has an unambiguous class.
    """

    operation = step.get("operation", {})
    operation = operation if isinstance(operation, dict) else {}
    declared = str(operation.get("execution_class") or "").lower()
    if declared in EXECUTION_CLASSES:
        return declared
    mode = str(operation.get("execution_mode") or "auto").lower()
    return {
        "method_hub": SOURCE_OPERATION_CLASS,
        "generated_code": DETERMINISTIC_TRANSFORM_CLASS,
        SEMANTIC_ANALYSIS_ROUTE: SEMANTIC_INFERENCE_CLASS,
    }.get(mode, AUTO_EXECUTION_CLASS)


def semantic_execution_required(step: dict[str, Any]) -> bool:
    """Return whether a PlanStep explicitly requires language reasoning."""

    return execution_class_for_step(step) == SEMANTIC_INFERENCE_CLASS


@dataclass(frozen=True, slots=True)
class ToolBindingResult:
    """A validated Method Hub argument set and its input lineage."""

    arguments: dict[str, Any]
    argument_bindings: dict[str, dict[str, str]]
    errors: tuple[str, ...] = ()


class ToolArgumentBinder:
    """Compile symbolic Router bindings into concrete Method Hub arguments.

    The Router owns the semantic choice. This component never selects a tool.
    It resolves declared input references, applies a small type-level adapter
    vocabulary, and validates the resulting values against the tool schema.
    """

    def bind(
        self,
        route: dict[str, Any],
        parameters_schema: Any,
        resolved_inputs: list[dict[str, Any]],
        *,
        sandbox: bool = False,
    ) -> ToolBindingResult:
        schema = parameters_schema if isinstance(parameters_schema, dict) else {}
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, dict) else {}
        required = {
            str(name)
            for name in schema.get("required", [])
            if str(name)
        }
        arguments = (
            deepcopy(route.get("arguments"))
            if isinstance(route.get("arguments"), dict)
            else {}
        )
        declared_bindings = (
            route.get("argument_bindings")
            if isinstance(route.get("argument_bindings"), dict)
            else {}
        )
        normalized_bindings: dict[str, dict[str, str]] = {}
        errors: list[str] = []
        inputs_by_ref = {
            str(item.get("ref")): item
            for item in resolved_inputs
            if isinstance(item, dict) and item.get("ref")
        }
        used_input_refs: set[str] = set()

        for parameter_name, declaration in declared_bindings.items():
            name = str(parameter_name)
            if name not in properties:
                errors.append(
                    f"Argument binding targets unknown parameter {name!r}."
                )
                continue
            binding_ref, adapter = self._binding_declaration(declaration)
            binding = inputs_by_ref.get(binding_ref)
            if binding is None:
                errors.append(
                    f"Argument {name!r} references unavailable input {binding_ref!r}."
                )
                continue
            if adapter not in ARGUMENT_ADAPTERS:
                errors.append(
                    f"Argument {name!r} requests unsupported adapter {adapter!r}."
                )
                continue
            if (
                adapter == "artifact_path"
                and self._parameter_role(name, properties[name]) != "path"
            ):
                errors.append(
                    f"Argument {name!r} is not a path parameter and cannot use "
                    "the artifact_path adapter."
                )
                continue
            if adapter == "artifact_path" and sandbox and not binding.get(
                "sandbox_path"
            ):
                errors.append(
                    f"Input {binding_ref!r} has no sandbox artifact path for "
                    f"generated-code parameter {name!r}."
                )
                continue
            value = self._adapt_value(
                binding,
                adapter,
                sandbox=sandbox,
            )
            if not self.value_matches_schema(value, properties[name]):
                errors.append(
                    f"Input {binding_ref!r} does not satisfy parameter {name!r} "
                    f"after adapter {adapter!r}."
                )
                continue
            arguments[name] = value
            normalized_bindings[name] = {
                "input_ref": binding_ref,
                "adapter": adapter,
            }
            used_input_refs.add(binding_ref)

        for parameter_name, parameter_schema in properties.items():
            name = str(parameter_name)
            if name in normalized_bindings:
                continue
            candidates = self._binding_candidates(
                name,
                parameter_schema,
                resolved_inputs,
                sandbox=sandbox,
            )
            if not candidates:
                continue
            best_score = candidates[0][0]
            best = [candidate for candidate in candidates if candidate[0] == best_score]
            supplied_value_is_valid = (
                name in arguments
                and self.value_matches_schema(arguments[name], parameter_schema)
            )
            should_bind = (
                len(best) == 1
                and (
                    not supplied_value_is_valid
                    or best_score >= 20
                    or name in required
                )
            )
            if not should_bind:
                continue
            _, binding, adapter = best[0]
            binding_ref = str(binding.get("ref"))
            if binding_ref in used_input_refs and name not in required:
                continue
            arguments[name] = self._adapt_value(
                binding,
                adapter,
                sandbox=sandbox,
            )
            normalized_bindings[name] = {
                "input_ref": binding_ref,
                "adapter": adapter,
            }
            used_input_refs.add(binding_ref)

        unknown_arguments = [
            str(name) for name in arguments if str(name) not in properties
        ]
        if schema.get("additionalProperties") is False:
            errors.extend(
                f"Tool schema does not allow argument {name!r}."
                for name in unknown_arguments
            )
        for name, value in arguments.items():
            if name in properties and not self.value_matches_schema(
                value, properties[name]
            ):
                errors.append(
                    f"Argument {name!r} does not satisfy the Method Hub schema."
                )
            if (
                name in properties
                and self._parameter_role(name, properties[name]) == "identity"
                and self._is_resolved_artifact_location(value, resolved_inputs)
            ):
                errors.append(
                    f"Argument {name!r} expects an identity selector, but received "
                    "a resolved artifact location."
                )
        for name in sorted(required):
            if name not in arguments:
                errors.append(f"Required Method Hub argument {name!r} is unbound.")

        return ToolBindingResult(
            arguments=arguments,
            argument_bindings=normalized_bindings,
            errors=tuple(dict.fromkeys(errors)),
        )

    @classmethod
    def value_matches_schema(cls, value: Any, schema: Any) -> bool:
        if not isinstance(schema, dict):
            return True
        alternatives = schema.get("anyOf") or schema.get("oneOf")
        if isinstance(alternatives, list):
            return any(cls.value_matches_schema(value, item) for item in alternatives)
        expected = schema.get("type")
        if isinstance(expected, list):
            return any(
                cls.value_matches_schema(value, {**schema, "type": item})
                for item in expected
            )
        checks = {
            "array": lambda item: isinstance(item, list),
            "boolean": lambda item: isinstance(item, bool),
            "integer": lambda item: isinstance(item, int)
            and not isinstance(item, bool),
            "null": lambda item: item is None,
            "number": lambda item: isinstance(item, (int, float))
            and not isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
            "string": lambda item: isinstance(item, str),
        }
        return checks.get(str(expected), lambda _item: True)(value)

    @staticmethod
    def _binding_declaration(value: Any) -> tuple[str, str]:
        if isinstance(value, str):
            return value, "identity"
        if not isinstance(value, dict):
            return "", "identity"
        return (
            str(value.get("input_ref") or value.get("ref") or ""),
            str(value.get("adapter") or "identity"),
        )

    def _binding_candidates(
        self,
        parameter_name: str,
        parameter_schema: Any,
        resolved_inputs: list[dict[str, Any]],
        *,
        sandbox: bool,
    ) -> list[tuple[int, dict[str, Any], str]]:
        candidates: list[tuple[int, dict[str, Any], str]] = []
        for binding in resolved_inputs:
            if not isinstance(binding, dict) or not binding.get("ref"):
                continue
            for adapter in ARGUMENT_ADAPTERS:
                if (
                    adapter == "artifact_path"
                    and sandbox
                    and not binding.get("sandbox_path")
                ):
                    continue
                if not self._binding_contract_matches_schema(
                    binding,
                    adapter,
                    parameter_schema,
                ):
                    continue
                value = self._adapt_value(binding, adapter, sandbox=sandbox)
                if value is None or not self.value_matches_schema(
                    value, parameter_schema
                ):
                    continue
                score = self._binding_score(
                    parameter_name,
                    parameter_schema,
                    binding,
                    adapter,
                )
                if score > 0:
                    candidates.append((score, binding, adapter))
        return sorted(
            candidates,
            key=lambda item: (
                -item[0],
                str(item[1].get("ref")),
                item[2],
            ),
        )

    @classmethod
    def _binding_contract_matches_schema(
        cls,
        binding: dict[str, Any],
        adapter: str,
        parameter_schema: Any,
    ) -> bool:
        """Check structural compatibility without inspecting data samples."""

        schema = parameter_schema if isinstance(parameter_schema, dict) else {}
        if adapter in {"artifact_path", "records_to_text"}:
            return cls.value_matches_schema("", schema)
        expected = schema.get("type")
        if isinstance(expected, list):
            expected_types = {str(item) for item in expected}
        elif expected:
            expected_types = {str(expected)}
        else:
            expected_types = set()
        actual_type = str(binding.get("json_type") or "").lower()
        if expected_types and actual_type and actual_type not in expected_types:
            return False
        if actual_type != "array" or not isinstance(schema.get("items"), dict):
            return True
        expected_item_types = schema["items"].get("type")
        expected_item_types = (
            {str(item) for item in expected_item_types}
            if isinstance(expected_item_types, list)
            else ({str(expected_item_types)} if expected_item_types else set())
        )
        if not expected_item_types:
            return True
        structure = binding.get("structure")
        structure = structure if isinstance(structure, dict) else {}
        item_structure = structure.get("item")
        if isinstance(item_structure, dict):
            actual_item_type = str(item_structure.get("type") or "").lower()
        else:
            aliases = {
                "bool": "boolean",
                "dict": "object",
                "float": "number",
                "int": "integer",
                "list": "array",
                "str": "string",
            }
            actual_item_type = aliases.get(
                str(item_structure or "").lower(),
                str(item_structure or "").lower(),
            )
        if not actual_item_type:
            return True
        if actual_item_type == "integer" and "number" in expected_item_types:
            return True
        return actual_item_type in expected_item_types

    @classmethod
    def _binding_score(
        cls,
        parameter_name: str,
        parameter_schema: Any,
        binding: dict[str, Any],
        adapter: str,
    ) -> int:
        parameter_tokens = cls._tokens(parameter_name)
        names = [
            str(binding.get("argument_name") or ""),
            str(binding.get("output_name") or ""),
            str(binding.get("source_step_id") or ""),
        ]
        normalized_parameter = "_".join(parameter_tokens)
        normalized_names = {"_".join(cls._tokens(name)) for name in names}
        score = 1
        if normalized_parameter in normalized_names:
            score += 100
        input_tokens = {
            token
            for value in names
            for token in cls._tokens(value)
        }
        role_tokens = {
            token
            for role in binding.get("semantic_roles", [])
            for token in cls._tokens(str(role))
        }
        score += 15 * len(parameter_tokens & input_tokens)
        score += 8 * len(parameter_tokens & role_tokens)
        if adapter == "artifact_path":
            if cls._parameter_role(parameter_name, parameter_schema) == "path":
                score += 30
            else:
                return 0
        elif adapter == "records_to_text":
            if not parameter_tokens & {"text", "content", "document", "corpus"}:
                return 0
            if {"source", "content"}.issubset(role_tokens):
                score += 30
            else:
                score += 10
        return score

    @classmethod
    def _parameter_role(cls, name: str, schema: Any) -> str:
        """Infer whether a string parameter is a path, identity, text, or value.

        This deliberately uses the parameter contract rather than concrete tool
        names. A filename, object key, or ID identifies a corpus object; it is
        not interchangeable with a local artifact path merely because both are
        represented as JSON strings.
        """

        schema = schema if isinstance(schema, dict) else {}
        name_tokens = cls._tokens(name)
        description_tokens = cls._tokens(
            " ".join(
                str(schema.get(field) or "")
                for field in ("title", "description", "format")
            )
        )
        all_tokens = name_tokens | description_tokens
        normalized_name = "_".join(cls._tokens(name))
        if (
            "path" in name_tokens
            or normalized_name.endswith("_path")
            or all_tokens
            & {
                "filesystem",
                "filepath",
                "pathname",
                "sandbox",
                "staged",
            }
        ):
            return "path"
        if (
            normalized_name.endswith(("_id", "_ids", "_name", "_names", "_key", "_keys"))
            or all_tokens
            & {
                "identifier",
                "identity",
                "selector",
                "filename",
                "objectkey",
            }
        ):
            return "identity"
        if name_tokens & {"text", "content", "markdown", "query"}:
            return "text"
        return "value"

    @staticmethod
    def _is_resolved_artifact_location(
        value: Any,
        resolved_inputs: list[dict[str, Any]],
    ) -> bool:
        if not isinstance(value, str) or not value.strip():
            return False
        known_locations = {
            str(binding.get(field))
            for binding in resolved_inputs
            if isinstance(binding, dict)
            for field in ("artifact_ref", "host_path", "sandbox_path")
            if binding.get(field)
        }
        return value in known_locations

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token.lower()
            for token in _TOKEN_PATTERN.findall(
                re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
            )
            if token
        }

    @classmethod
    def _adapt_value(
        cls,
        binding: dict[str, Any],
        adapter: str,
        *,
        sandbox: bool,
    ) -> Any:
        if adapter == "identity":
            return binding.get("value")
        if adapter == "artifact_path":
            return (
                binding.get("sandbox_path")
                if sandbox
                else binding.get("host_path")
            ) or binding.get("artifact_ref")
        if adapter == "records_to_text":
            return cls._text_content(binding.get("value"))
        return None

    @classmethod
    def _text_content(cls, value: Any) -> str:
        parts: list[str] = []

        def collect(item: Any) -> None:
            if isinstance(item, str):
                rendered = item.strip()
                if rendered:
                    parts.append(rendered)
                return
            if isinstance(item, list):
                for child in item:
                    collect(child)
                return
            if isinstance(item, dict):
                preferred = [
                    item.get(name)
                    for name in ("text", "content", "markdown", "value")
                    if isinstance(item.get(name), str)
                ]
                if preferred:
                    for child in preferred:
                        collect(child)
                    return
                for child in item.values():
                    collect(child)

        collect(value)
        return "\n\n".join(dict.fromkeys(parts))


class ReportContractValidator:
    """Validate normalized Plan and Template contracts before scheduling."""

    def validate_plan(self, plan: dict[str, Any]) -> list[str]:
        steps = [
            item for item in plan.get("steps", []) if isinstance(item, dict)
        ]
        errors: list[str] = []
        step_ids = [str(step.get("step_id") or "") for step in steps]
        if any(not step_id for step_id in step_ids):
            errors.append("Every PlanStep must have a non-empty step_id.")
        duplicates = {
            step_id for step_id in step_ids if step_ids.count(step_id) > 1
        }
        errors.extend(
            f"PlanStep id {step_id!r} is duplicated."
            for step_id in sorted(duplicates)
        )
        valid_ids = set(step_ids)
        valid_refs = {
            f"step-output://{step.get('step_id')}/{output.get('name')}"
            for step in steps
            for output in step.get("outputs", [])
            if isinstance(output, dict) and output.get("name")
        }
        dependencies: dict[str, set[str]] = {}
        for step in steps:
            step_id = str(step.get("step_id") or "")
            operation = step.get("operation")
            operation = operation if isinstance(operation, dict) else {}
            mode = str(operation.get("execution_mode") or "auto")
            if mode not in EXECUTION_MODES:
                errors.append(
                    f"PlanStep {step_id!r} has unsupported execution_mode {mode!r}."
                )
            execution_class = str(
                operation.get("execution_class") or AUTO_EXECUTION_CLASS
            )
            if execution_class not in EXECUTION_CLASSES:
                errors.append(
                    f"PlanStep {step_id!r} has unsupported execution_class "
                    f"{execution_class!r}."
                )
            incompatible_modes = {
                SOURCE_OPERATION_CLASS: {
                    "generated_code",
                    SEMANTIC_ANALYSIS_ROUTE,
                },
                DETERMINISTIC_TRANSFORM_CLASS: {SEMANTIC_ANALYSIS_ROUTE},
                SEMANTIC_INFERENCE_CLASS: {
                    "method_hub",
                    "generated_code",
                },
            }
            if mode in incompatible_modes.get(execution_class, set()):
                errors.append(
                    f"PlanStep {step_id!r} execution_mode {mode!r} conflicts "
                    f"with execution_class {execution_class!r}."
                )
            step_dependencies = {
                str(item) for item in step.get("depends_on", []) if str(item)
            }
            unknown = step_dependencies - valid_ids
            errors.extend(
                f"PlanStep {step_id!r} depends on unknown step {item!r}."
                for item in sorted(unknown)
            )
            dependencies[step_id] = step_dependencies & valid_ids
            output_names = [
                str(output.get("name") or "")
                for output in step.get("outputs", [])
                if isinstance(output, dict)
            ]
            if not output_names:
                errors.append(f"PlanStep {step_id!r} declares no named output.")
            if len(output_names) != len(set(output_names)):
                errors.append(f"PlanStep {step_id!r} has duplicate output names.")
            for output in step.get("outputs", []):
                if not isinstance(output, dict):
                    errors.append(
                        f"PlanStep {step_id!r} contains a non-object output contract."
                    )
                    continue
                output_name = str(output.get("name") or "")
                output_type = str(output.get("type") or "")
                output_shape = str(output.get("shape") or "")
                expected_type = {
                    "array": "array",
                    "list": "array",
                    "table": "array",
                    "time_series": "array",
                    "category_series": "array",
                    "record": "object",
                }.get(output_shape)
                if expected_type and output_type != expected_type:
                    errors.append(
                        f"PlanStep {step_id!r} output {output_name!r} declares "
                        f"shape {output_shape!r} but JSON type {output_type!r}; "
                        f"expected {expected_type!r}."
                    )

            declared_input_dependencies: set[str] = set()
            input_names: list[str] = []
            for item in step.get("inputs", []):
                if not isinstance(item, dict):
                    errors.append(
                        f"PlanStep {step_id!r} contains a non-object input binding."
                    )
                    continue
                input_name = str(item.get("name") or "")
                if input_name:
                    input_names.append(input_name)
                ref = str(item.get("ref") or "")
                match = _STEP_OUTPUT_REF.match(ref)
                if match is None:
                    continue
                dependency = match.group(1)
                declared_input_dependencies.add(dependency)
                if ref not in valid_refs:
                    errors.append(
                        f"PlanStep {step_id!r} input {input_name!r} references "
                        f"unavailable output {ref!r}."
                    )
                if dependency not in step_dependencies:
                    errors.append(
                        f"PlanStep {step_id!r} input {input_name!r} references "
                        f"step {dependency!r} without declaring it as a dependency."
                    )
            if len(input_names) != len(set(input_names)):
                errors.append(f"PlanStep {step_id!r} has duplicate input names.")
            missing_refs = step_dependencies - declared_input_dependencies
            errors.extend(
                f"PlanStep {step_id!r} dependency {item!r} has no input binding."
                for item in sorted(missing_refs)
            )
        errors.extend(self._cycle_errors(dependencies))
        return list(dict.fromkeys(errors))

    def validate_template_bindings(
        self,
        plan: dict[str, Any],
        template_instance: dict[str, Any],
    ) -> list[str]:
        valid_refs = {
            f"step-output://{step.get('step_id')}/{output.get('name')}"
            for step in plan.get("steps", [])
            if isinstance(step, dict)
            for output in step.get("outputs", [])
            if isinstance(output, dict)
        }
        errors: list[str] = []
        for binding in template_instance.get("bindings", []):
            if not isinstance(binding, dict):
                continue
            refs = binding.get("plan_output_refs")
            refs = refs if isinstance(refs, list) else [binding.get("plan_output_ref")]
            for ref in refs:
                if ref and str(ref) not in valid_refs:
                    errors.append(
                        f"Template requirement {binding.get('requirement_ref')!r} "
                        f"references unavailable output {str(ref)!r}."
                    )
        return list(dict.fromkeys(errors))

    @staticmethod
    def _cycle_errors(dependencies: dict[str, set[str]]) -> list[str]:
        remaining = {key: set(value) for key, value in dependencies.items()}
        while remaining:
            ready = {key for key, value in remaining.items() if not value}
            if not ready:
                return [
                    "ReportPlan contains a dependency cycle involving: "
                    + ", ".join(sorted(remaining))
                ]
            for key in ready:
                remaining.pop(key, None)
            for value in remaining.values():
                value.difference_update(ready)
        return []
