"""Custom exception hierarchy for Walkmarr."""


class WalkmarrError(Exception):
    """Base exception for Walkmarr failures."""


class ConfigError(WalkmarrError):
    """Raised for config discovery, parsing, or validation errors."""


class PathMappingError(WalkmarrError):
    """Raised when a provider path cannot be mapped to a local path."""


class ProviderError(WalkmarrError):
    """Raised for provider API and matching errors."""


class ConversionError(WalkmarrError):
    """Raised for ffprobe/ffmpeg processing failures."""


class TaggingError(WalkmarrError):
    """Raised for AtomicParsley tagging failures."""
