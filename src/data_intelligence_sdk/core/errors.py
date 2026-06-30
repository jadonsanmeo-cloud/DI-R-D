"""Shared exception types for package boundaries."""


class DataIntelligenceError(Exception):
    """Base exception for the data intelligence package."""


class EngineNotFoundError(DataIntelligenceError):
    """Raised when the registry cannot find a suitable engine."""


class SpecConfirmationRequired(DataIntelligenceError):
    """Raised when a spec requires user confirmation before execution."""
