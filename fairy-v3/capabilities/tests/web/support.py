from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fairy_capabilities.web.fetch import RawHttpResponse
from fairy_capabilities.web.url_guard import AuthorizedUrl

PUBLIC = "93.184.216.34"


class StaticResolver:
    def __init__(self, answers: Mapping[str, tuple[str, ...]]) -> None:
        self.answers = dict(answers)
        self.calls: list[tuple[str, int]] = []

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.calls.append((host, port))
        return self.answers.get(host, ())


@dataclass(frozen=True, slots=True)
class ScriptedResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes | tuple[bytes, ...] = b""
    peer_ip: str = "93.184.216.34"


class ScriptedTransport:
    def __init__(self, responses: Iterable[ScriptedResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[AuthorizedUrl] = []

    def request(
        self,
        target: AuthorizedUrl,
        *,
        timeout_seconds: float,
    ) -> RawHttpResponse:
        del timeout_seconds
        self.requests.append(target)
        scripted = self.responses.pop(0)
        chunks = scripted.body if isinstance(scripted.body, tuple) else (scripted.body,)
        return RawHttpResponse(
            status_code=scripted.status_code,
            headers=scripted.headers,
            body=iter(chunks),
            peer_ip=scripted.peer_ip,
        )
