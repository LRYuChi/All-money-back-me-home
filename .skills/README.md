# Agent Skills

Curated agent skills from [awesome-agent-skills](https://github.com/VoltAgent/awesome-agent-skills) for best practices when working on this trading strategy advisory system.

## Installed Skills

| Skill | Source | Description |
|-------|--------|-------------|
| [supabase-postgres-best-practices](supabase-postgres-best-practices.md) | [supabase/agent-skills](https://github.com/supabase/agent-skills) | Postgres performance optimization: indexes, connection pooling, RLS, schema design |
| [next-best-practices](next-best-practices.md) | [vercel-labs/next-skills](https://github.com/vercel-labs/next-skills) | Next.js patterns: file conventions, RSC boundaries, data fetching, error handling |
| [next-cache-components](next-cache-components.md) | [vercel-labs/next-skills](https://github.com/vercel-labs/next-skills) | Cache Components / PPR for Next.js 16+: `use cache` directive, cacheLife, cacheTag |
| [react-best-practices](react-best-practices.md) | [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills) | 40+ React performance rules: waterfalls, bundle size, SSR, re-renders, advanced patterns |
| [fastapi-router-py](fastapi-router-py.md) | [microsoft/skills](https://github.com/microsoft/skills) | FastAPI router patterns: CRUD, authentication, response models, error handling |
| [pydantic-models-py](pydantic-models-py.md) | [microsoft/skills](https://github.com/microsoft/skills) | Pydantic v2 multi-model pattern: Base, Create, Update, Response, InDB variants |
| [binance-crypto-market-rank](binance-crypto-market-rank.md) | [binance/binance-skills-hub](https://github.com/binance/binance-skills-hub) | Crypto market rankings: trending tokens, smart money, meme rank, PnL leaderboards |
| [binance-spot](binance-spot.md) | [binance/binance-skills-hub](https://github.com/binance/binance-skills-hub) | Binance Spot trading API: orders, market data, authentication, 60+ endpoints |
| [stripe](stripe.md) | [stripe/ai](https://github.com/stripe/ai) | Stripe integration: Checkout Sessions, Payment Element, Connect, Billing, Webhooks |

## Usage

These skills are automatically referenced by AI coding agents when working on relevant parts of the codebase:

- **Database work** -> supabase-postgres-best-practices
- **Frontend (Next.js)** -> next-best-practices, next-cache-components
- **React components** -> react-best-practices
- **Python API** -> fastapi-router-py, pydantic-models-py
- **Trading features** -> binance-crypto-market-rank, binance-spot
- **Payments** -> stripe
