"""L6 Execution layer.

Subpackages:
  - pending_orders/  — middleware queue between StrategyIntent and exchange
                       dispatch. Persists every intent so workers can
                       retry / pause / be human-vetoed independently.
  - (future) okx/    — OKX adapter (Phase F.1)
  - (future) ibkr/   — IBKR adapter for US stocks (Phase F.2)
  - (future) tw_stock/ — TW broker adapter (Phase F.3)
"""
