from __future__ import annotations

import re

from fairy_core.assistant.interpretation import (
    ClassifierInterpretationPayload,
    InputSegmentKind,
    RequestAction,
    segment_user_input,
)
from fairy_core.assistant.routing import RoutingTaskKind

_BROWSER_SUBJECT = re.compile(
    r"(?:browser|web\s?page|website|preview|viewport|"
    r"\u6d4f\u89c8\u5668|\u7f51\u9875|\u9875\u9762|\u9884\u89c8)",
    re.IGNORECASE,
)
_BROWSER_ACTION = re.compile(
    r"(?:inspect|check|test|click|scroll|navigate|open|capture|screenshot|"
    r"\u68c0\u67e5|\u6d4b\u8bd5|\u70b9\u51fb|\u6eda\u52a8|\u6d4f\u89c8|"
    r"\u6253\u5f00|\u622a\u56fe|\u622a\u53d6)",
    re.IGNORECASE,
)
_WORKSPACE_MUTATION = re.compile(
    r"(?:fix|repair|edit|modify|update|change|rewrite|"
    r"\u4fee\u590d|\u4fee\u6539|\u66f4\u65b0|\u8c03\u6574|\u6539\u5199)",
    re.IGNORECASE,
)
_MEDIA_GENERATION = {
    RoutingTaskKind.IMAGE: re.compile(
        r"(?:"
        r"(?:generate|create|draw|illustrate|render|design|make)\s+(?:an?\s+|the\s+)?"
        r"(?:image|picture|illustration|poster|photo|artwork)"
        r"|(?:image|picture|illustration|poster|photo|artwork)\s+(?:generation|creation)"
        r"|(?:\u751f\u6210|\u521b\u5efa|\u7ed8\u5236|\u753b|\u8bbe\u8ba1|\u5236\u4f5c)"
        r".{0,12}(?:\u56fe\u7247|\u56fe\u50cf|\u63d2\u753b|\u6d77\u62a5|"
        r"\u7167\u7247|\u58c1\u7eb8)"
        r"|(?:\u56fe\u7247|\u56fe\u50cf|\u63d2\u753b|\u6d77\u62a5|\u7167\u7247|"
        r"\u58c1\u7eb8).{0,12}(?:\u751f\u6210|\u521b\u5efa|\u7ed8\u5236|\u5236\u4f5c)"
        r")",
        re.IGNORECASE,
    ),
    RoutingTaskKind.MUSIC: re.compile(
        r"(?:"
        r"(?:generate|create|compose|make)\s+(?:a\s+|the\s+)?(?:song|music|track|soundtrack)"
        r"|(?:\u751f\u6210|\u521b\u4f5c|\u5236\u4f5c|\u8c31\u5199).{0,12}"
        r"(?:\u97f3\u4e50|\u6b4c\u66f2|\u914d\u4e50|\u97f3\u8f68)"
        r")",
        re.IGNORECASE,
    ),
    RoutingTaskKind.VIDEO: re.compile(
        r"(?:"
        r"(?:generate|create|render|make)\s+(?:a\s+|the\s+)?(?:video|animation|clip)"
        r"|(?:\u751f\u6210|\u521b\u5efa|\u5236\u4f5c|\u6e32\u67d3).{0,12}"
        r"(?:\u89c6\u9891|\u52a8\u753b|\u77ed\u7247)"
        r")",
        re.IGNORECASE,
    ),
}


def guarded_task_kind(
    *,
    user_request: str,
    routed_kind: RoutingTaskKind,
    interpretation: ClassifierInterpretationPayload | None = None,
) -> RoutingTaskKind:
    """Fail closed when a specialized route lacks its explicit deliverable."""

    if interpretation is not None:
        if interpretation.action is RequestAction.BROWSE:
            return RoutingTaskKind.BROWSER
        if (
            interpretation.action is RequestAction.CHANGE
            and routed_kind is RoutingTaskKind.BROWSER
        ):
            return RoutingTaskKind.CODE
        if interpretation.action is RequestAction.GENERATE:
            return (
                routed_kind
                if routed_kind
                in {RoutingTaskKind.IMAGE, RoutingTaskKind.MUSIC, RoutingTaskKind.VIDEO}
                else RoutingTaskKind.GENERAL
            )
        if routed_kind in {
            RoutingTaskKind.IMAGE,
            RoutingTaskKind.MUSIC,
            RoutingTaskKind.VIDEO,
        }:
            return RoutingTaskKind.GENERAL
        if routed_kind is RoutingTaskKind.BROWSER:
            return RoutingTaskKind.GENERAL
        return routed_kind

    actionable_text = "".join(
        segment.text
        for segment in segment_user_input(user_request)
        if segment.kind is InputSegmentKind.TEXT
    )
    browser_qa = bool(_BROWSER_SUBJECT.search(actionable_text)) and bool(
        _BROWSER_ACTION.search(actionable_text)
    )
    if browser_qa:
        return (
            RoutingTaskKind.CODE
            if _WORKSPACE_MUTATION.search(actionable_text)
            else RoutingTaskKind.BROWSER
        )
    media_pattern = _MEDIA_GENERATION.get(routed_kind)
    if media_pattern is not None and media_pattern.search(actionable_text) is None:
        return RoutingTaskKind.GENERAL
    return routed_kind


__all__ = ["guarded_task_kind"]
