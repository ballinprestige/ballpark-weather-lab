class BallparkError(RuntimeError):
    """Base error for an expected Ballpark pipeline failure."""


class SourceUnavailable(BallparkError):
    """A required external source could not be reached or validated."""


class ArtifactError(BallparkError):
    """A local model or evidence artifact is missing, malformed, or changed."""


class DataContractError(BallparkError):
    """A payload violates the standalone publication contract."""


class PublicVerificationError(BallparkError):
    """The public site does not yet match the locally built release receipt."""

