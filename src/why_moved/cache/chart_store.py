"""차트 PNG 저장소 — 결정적 chart_id로 저장·서빙하고 오래된 파일을 청소한다."""

import hashlib
import time
from pathlib import Path

_TTL_SECONDS = 86400


class ChartStore:
    def __init__(self, base_dir: str):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def chart_id(self, *key_parts: str) -> str:
        return hashlib.sha1(":".join(key_parts).encode()).hexdigest()[:16]

    def save(self, png: bytes, *key_parts: str) -> str:
        """PNG 저장 후 chart_id 반환. 동일 키는 덮어쓴다 (결정적 캐시)."""
        chart_id = self.chart_id(*key_parts)
        (self._dir / f"{chart_id}.png").write_bytes(png)
        self._cleanup()
        return chart_id

    def exists(self, *key_parts: str) -> str | None:
        """이미 생성된 차트면 chart_id 반환 (재생성 생략용)."""
        chart_id = self.chart_id(*key_parts)
        return chart_id if (self._dir / f"{chart_id}.png").exists() else None

    def path(self, chart_id: str) -> Path | None:
        """서빙용 경로. 경로 탈출 방지를 위해 hex id만 허용한다."""
        if not chart_id.isalnum():
            return None
        p = self._dir / f"{chart_id}.png"
        return p if p.exists() else None

    def _cleanup(self) -> None:
        cutoff = time.time() - _TTL_SECONDS
        for f in self._dir.glob("*.png"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
