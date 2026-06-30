"""Runtime cache boundary."""


class Cache:
    """Placeholder cache contract."""

    def get(self, key: str) -> object | None:
        raise NotImplementedError("Cache backend is not part of the base scaffold.")

    def set(self, key: str, value: object) -> None:
        raise NotImplementedError("Cache backend is not part of the base scaffold.")
