import io
from dataclasses import dataclass

from services.underwriting.models import WorkflowError

from .documents import JPEG


@dataclass(frozen=True)
class ImageConfig:
    max_side: int = 2400
    quality: int = 85


class ImageNormalizer:
    def __init__(self, cfg: ImageConfig | None = None):
        self.cfg = cfg or ImageConfig()

    def normalize(self, content: bytes) -> tuple[str, bytes]:
        from PIL import Image, ImageOps, UnidentifiedImageError
        from pillow_heif import register_heif_opener

        register_heif_opener()
        try:
            with Image.open(io.BytesIO(content)) as image:
                upright = ImageOps.exif_transpose(image).convert("RGB")
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise WorkflowError("STATEMENT_IMAGE_UNREADABLE", 422) from exc
        upright.thumbnail((self.cfg.max_side, self.cfg.max_side))
        buffer = io.BytesIO()
        upright.save(buffer, format="JPEG", quality=self.cfg.quality, optimize=True)
        return JPEG, buffer.getvalue()
