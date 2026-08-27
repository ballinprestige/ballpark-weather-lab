from __future__ import annotations

from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class StdlibBytesClient:
    """Dependency-free, bounded client used only by the post-deployment readback job."""

    timeout: float = 12.0
    maximum_bytes: int = 10 * 1024 * 1024
    user_agent: str = "ballpark-weather-lab-public-verifier/1.0"

    def get_bytes(self, url: str) -> bytes:
        request = Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=self.timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > self.maximum_bytes:
                raise ValueError("public verification response exceeds the size limit")
            content = response.read(self.maximum_bytes + 1)
        if len(content) > self.maximum_bytes:
            raise ValueError("public verification response exceeds the size limit")
        return content
