"""Signal pipeline (Phase 4+): raw HL fills → classified signals → aggregated follow-orders.

Submodules:
- types: shared dataclasses + enums (no logic)
- dispatcher: parse raw HL fill dicts → RawFillEvent with latency timestamps
- classifier (P4b, pending): position state-machine, raw → Signal
- aggregator (P4c, pending): multi-wallet merge, Signal → FollowOrder
- whitelist (P4b, pending): dynamic whitelist with manual override + freshness
"""
