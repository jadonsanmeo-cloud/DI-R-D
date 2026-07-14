"""Sandbox log storage boundary."""


class LogStore:
    """Records logs from sandboxed execution."""

    def append(self, message: str) -> None:
        raise NotImplementedError("Log storage is not part of the base scaffold.")
