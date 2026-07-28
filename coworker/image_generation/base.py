from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


class ImageGenerationError(RuntimeError):
    """Base error for image generation without provider response details or secrets."""


class ImageAuthError(ImageGenerationError):
    """Image provider credentials are missing or rejected."""


class ImageRateLimitError(ImageGenerationError):
    """The image provider rejected the request because of its rate limit."""


class ImageGenerationTimeout(ImageGenerationError):
    """The image provider or its controlled image download timed out."""


class ImageResponseError(ImageGenerationError):
    """The provider returned an unusable response or invalid image."""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    output_path: Path
    aspect_ratio: str = "1:1"
    quality: str = "high"

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("image prompt must not be blank")
        object.__setattr__(self, "output_path", Path(self.output_path))


@dataclass(frozen=True)
class ImageResult:
    path: Path
    provider: str
    model: str
    sha256: str


class ImageGenerationProvider(ABC):
    @abstractmethod
    async def generate(self, request: ImageRequest) -> ImageResult:
        """Generate one validated image and atomically save it to request.output_path."""


__all__ = [
    "ImageAuthError",
    "ImageGenerationError",
    "ImageGenerationProvider",
    "ImageGenerationTimeout",
    "ImageRateLimitError",
    "ImageRequest",
    "ImageResponseError",
    "ImageResult",
]
