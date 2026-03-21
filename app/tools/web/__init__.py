"""app.tools.web"""
from app.tools.web.url_normalizer import normalize_url, is_valid_url
from app.tools.web.source_classifier import classify_source
from app.tools.web.domain_rules import domain_trust, HIGH_TRUST, LOW_TRUST
__all__ = ["normalize_url", "is_valid_url", "classify_source",
           "domain_trust", "HIGH_TRUST", "LOW_TRUST"]
