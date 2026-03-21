"""Query analyzer — classifies research intent and detects query properties."""
from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class ResearchIntent:
    subtype: str = "general"        # news/product/docs/comparison/company/general
    recency_required: bool = False
    official_preferred: bool = False
    is_comparison: bool = False
    is_verification: bool = False
    primary_entity: str = ""
    secondary_entity: str = ""
    language_hint: str = "zh"


_NEWS_KW    = re.compile(r"\u65b0\u95fb|\u6700\u65b0|today|latest|news|\u516c\u544a|\u53d1\u5e03", re.I)
_DOCS_KW    = re.compile(r"\u6587\u6863|api|\u4f7f\u7528\u624b\u518c|how to|tutorial|documentation|spec", re.I)
_PRODUCT_KW = re.compile(r"\u4ef7\u683c|\u8bc4\u6d4b|\u8bc4\u5224|review|buy|\u8d2d\u4e70|product|\u53c2\u6570|spec", re.I)
_COMPANY_KW = re.compile(r"\u516c\u53f8|\u4f01\u4e1a|company|corp|\u5c55\u671f|about", re.I)
_COMPARE_KW = re.compile(r"vs|\u5bf9\u6bd4|compare|\u533a\u522b|\u4e0e.*\u7684\u5dee\u5f02|difference", re.I)
_VERIFY_KW  = re.compile(r"\u662f\u5426|\u771f\u7684|\u786e\u8ba4|verify|confirm|\u5c0f\u9053\u6d88\u606f|\u771f\u5047", re.I)
_OFFICIAL_KW = re.compile(r"\u5b98\u65b9|official|\u6b63\u5f0f\u516c\u544a|\u5b98\u7f51", re.I)


class QueryAnalyzer:
    """Classify a research query into a ResearchIntent."""

    def analyze(self, query: str) -> ResearchIntent:
        q = query or ""
        intent = ResearchIntent()

        # Subtype detection (order matters — most specific first)
        if _COMPARE_KW.search(q):
            intent.subtype = "comparison"
            intent.is_comparison = True
        elif _NEWS_KW.search(q):
            intent.subtype = "news"
            intent.recency_required = True
        elif _DOCS_KW.search(q):
            intent.subtype = "docs"
        elif _PRODUCT_KW.search(q):
            intent.subtype = "product"
        elif _COMPANY_KW.search(q):
            intent.subtype = "company"
        else:
            intent.subtype = "general"

        if _VERIFY_KW.search(q):
            intent.is_verification = True
        if _OFFICIAL_KW.search(q):
            intent.official_preferred = True

        # Detect recency signals
        if re.search(r"\u73b0\u5728|\u4eca\u5929|\u8fd1\u671f|\u6700\u65b0|recent|current|today|now", q, re.I):
            intent.recency_required = True

        # Detect language
        if re.search(r"[\u4e00-\u9fff]", q):
            intent.language_hint = "zh"
        else:
            intent.language_hint = "en"

        return intent
