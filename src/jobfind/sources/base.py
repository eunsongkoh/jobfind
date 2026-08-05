from abc import ABC, abstractmethod

from ..config import AppConfig, TrackDefinition
from ..models import Job


class BaseSource(ABC):
    """Common interface every discovery source implements.

    Adding a new source = one new file implementing fetch() + one entry in
    SOURCE_REGISTRY + one name in config.yaml's active_sources. Nothing else
    in the pipeline needs to change.
    """

    name: str

    def __init__(self, track: str, track_def: TrackDefinition, app_config: AppConfig):
        self.track = track
        self.track_def = track_def
        self.app_config = app_config

    @abstractmethod
    def fetch(self) -> list[Job]:
        """Return jobs for this track.

        Must catch its own sub-failures (one blocked site, one dead ATS board)
        and return partial results — never raise out of the pipeline.
        """
