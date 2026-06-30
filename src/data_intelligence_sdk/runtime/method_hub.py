"""Method hub boundary for engine-accessible capabilities."""


class MethodHub:
    """Registry for methods that engines may call during execution."""

    def __init__(self) -> None:
        self._methods: dict[str, object] = {}

    def register(self, name: str, method: object) -> None:
        self._methods[name] = method

    def get(self, name: str) -> object:
        return self._methods[name]
