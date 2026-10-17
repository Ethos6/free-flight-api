"""FreeFlight API — free, self-hosted live flight tracking API."""

__version__ = "1.0.0"

from .config import Settings  # noqa: E402
from .core import FreeFlight  # noqa: E402
from .models import Aircraft, FlightsResponse  # noqa: E402

__all__ = ["Aircraft", "FlightsResponse", "FreeFlight", "Settings", "__version__"]
