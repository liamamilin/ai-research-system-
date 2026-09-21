"""Shared exception types for the research engine."""


class CancelledError(Exception):
    """Raised when a job is cancelled via the cancel token."""


class LLMError(Exception):
    """Base error for LLM API failures."""


class LLMConfigError(LLMError):
    """Raised when the LLM configuration is incomplete or invalid."""


class LLMUnsupportedError(LLMError):
    """Raised when the model/endpoint rejects tool calling.

    Signals the research agent to fall back to pipeline mode.
    """
