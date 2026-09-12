"""Default for newly created runs only; stored engine versions remain authoritative."""

# New runs use real node boundaries. Keep the v3 adapter while any persisted
# v3 run is nonterminal; never rewrite an existing run's engine.
DEFAULT_ASSISTANT_ENGINE_VERSION = 4
