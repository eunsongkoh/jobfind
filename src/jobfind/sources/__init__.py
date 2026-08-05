from .ats_source import AtsSource
from .base import BaseSource
from .github_tracker_source import GithubTrackerSource
from .jobspy_source import JobSpySource

SOURCE_REGISTRY: dict[str, type[BaseSource]] = {
    JobSpySource.name: JobSpySource,
    GithubTrackerSource.name: GithubTrackerSource,
    AtsSource.name: AtsSource,
}
