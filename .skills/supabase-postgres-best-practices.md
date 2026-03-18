---
name: supabase-postgres-best-practices
description: Postgres performance optimization and best practices from Supabase. Use this skill when writing, reviewing, or optimizing Postgres queries, schema designs, or database configurations.
license: MIT
metadata:
  author: supabase
  version: "1.1.0"
  organization: Supabase
  date: January 2026
  abstract: Comprehensive Postgres performance optimization guide for developers using Supabase and Postgres. Contains performance rules across 8 categories, prioritized by impact from critical (query performance, connection management) to incremental (advanced features). Each rule includes detailed explanations, incorrect vs. correct SQL examples, query plan analysis, and specific performance metrics to guide automated optimization and code generation.
source: https://github.com/supabase/agent-skills/tree/main/skills/supabase-postgres-best-practices
---

# Supabase Postgres Best Practices

Comprehensive performance optimization guide for Postgres, maintained by Supabase. Contains rules across 8 categories, prioritized by impact to guide automated query optimization and schema design.

## When to Apply

Reference these guidelines when:
- Writing SQL queries or designing schemas
- Implementing indexes or query optimization
- Reviewing database performance issues
- Configuring connection pooling or scaling
- Optimizing for Postgres-specific features
- Working with Row-Level Security (RLS)

## Rule Categories by Priority

| Priority | Category | Impact | Prefix |
|----------|----------|--------|--------|
| 1 | Query Performance | CRITICAL | `query-` |
| 2 | Connection Management | CRITICAL | `conn-` |
| 3 | Security & RLS | CRITICAL | `security-` |
| 4 | Schema Design | HIGH | `schema-` |
| 5 | Concurrency & Locking | MEDIUM-HIGH | `lock-` |
| 6 | Data Access Patterns | MEDIUM | `data-` |
| 7 | Monitoring & Diagnostics | LOW-MEDIUM | `monitor-` |
| 8 | Advanced Features | LOW | `advanced-` |

## How to Use

Each rule contains:
- Brief explanation of why it matters
- Incorrect SQL example with explanation
- Correct SQL example with explanation
- Optional EXPLAIN output or metrics
- Additional context and references
- Supabase-specific notes (when applicable)

---

## CRITICAL: Add Indexes on WHERE and JOIN Columns

Queries filtering or joining on unindexed columns cause full table scans, which become exponentially slower as tables grow.

**Incorrect (sequential scan on large table):**

```sql
-- No index on customer_id causes full table scan
select * from orders where customer_id = 123;

-- EXPLAIN shows: Seq Scan on orders (cost=0.00..25000.00 rows=100 width=85)
```

**Correct (index scan):**

```sql
-- Create index on frequently filtered column
create index orders_customer_id_idx on orders (customer_id);

select * from orders where customer_id = 123;

-- EXPLAIN shows: Index Scan using orders_customer_id_idx (cost=0.42..8.44 rows=100 width=85)
```

For JOIN columns, always index the foreign key side:

```sql
-- Index the referencing column
create index orders_customer_id_idx on orders (customer_id);

select c.name, o.total
from customers c
join orders o on o.customer_id = c.id;
```

---

## CRITICAL: Use Connection Pooling for All Applications

Postgres connections are expensive (1-3MB RAM each). Without pooling, applications exhaust connections under load.

**Incorrect (new connection per request):**

```sql
-- Each request creates a new connection
-- Result: 500 concurrent users = 500 connections = crashed database
select count(*) from pg_stat_activity;  -- 487 connections!
```

**Correct (connection pooling):**

```sql
-- Use a pooler like PgBouncer between app and database
-- Configure pool_size based on: (CPU cores * 2) + spindle_count
-- Example for 4 cores: pool_size = 10
-- Result: 500 concurrent users share 10 actual connections
select count(*) from pg_stat_activity;  -- 10 connections
```

Pool modes:
- **Transaction mode**: connection returned after each transaction (best for most apps)
- **Session mode**: connection held for entire session (needed for prepared statements, temp tables)

---

## CRITICAL: Enable Row Level Security for Multi-Tenant Data

Row Level Security (RLS) enforces data access at the database level, ensuring users only see their own data.

**Incorrect (application-level filtering only):**

```sql
-- Relying only on application to filter
select * from orders where user_id = $current_user_id;
-- Bug or bypass means all data is exposed!
```

**Correct (database-enforced RLS):**

```sql
-- Enable RLS on the table
alter table orders enable row level security;

-- Create policy for users to see only their orders
create policy orders_user_policy on orders
  for all
  using (user_id = current_setting('app.current_user_id')::bigint);

-- Force RLS even for table owners
alter table orders force row level security;
```

Policy for authenticated role:

```sql
create policy orders_user_policy on orders
  for all
  to authenticated
  using (user_id = auth.uid());
```

---

## HIGH: Select Optimal Primary Key Strategy

Primary key choice affects insert performance, index size, and replication efficiency.

**Correct (optimal PK strategies):**

```sql
-- Use IDENTITY for sequential IDs (SQL-standard, best for most cases)
create table users (
  id bigint generated always as identity primary key
);

-- For distributed systems needing UUIDs, use UUIDv7 (time-ordered)
create table orders (
  id uuid default uuid_generate_v7() primary key
);
```

Guidelines:
- Single database: `bigint identity` (sequential, 8 bytes, SQL-standard)
- Distributed/exposed IDs: UUIDv7 or ULID (time-ordered, no fragmentation)
- Avoid random UUIDs (v4) as primary keys on large tables (causes index fragmentation)

---

## MEDIUM-HIGH: Eliminate N+1 Queries with Batch Loading

N+1 queries execute one query per item in a loop. Batch them into a single query.

**Incorrect (N+1 queries):**

```sql
-- First query: get all users
select id from users where active = true;  -- Returns 100 IDs
-- Then N queries, one per user
select * from orders where user_id = 1;
-- ... 99 more queries! Total: 101 round trips
```

**Correct (single batch query):**

```sql
-- Use ANY with array
select * from orders where user_id = any(array[1, 2, 3, ...]);

-- Or use JOIN
select u.id, u.name, o.*
from users u
left join orders o on o.user_id = u.id
where u.active = true;
-- Total: 1 round trip
```

---

## Additional Rule Categories

For comprehensive rules, see the full reference at:
https://github.com/supabase/agent-skills/tree/main/skills/supabase-postgres-best-practices/references

Key reference files:
- `query-composite-indexes.md` - Multi-column index strategies
- `query-covering-indexes.md` - Include columns in indexes
- `query-partial-indexes.md` - Conditional indexes for subsets
- `conn-idle-timeout.md` - Managing idle connections
- `conn-limits.md` - Setting appropriate connection limits
- `schema-constraints.md` - CHECK, UNIQUE, NOT NULL constraints
- `schema-data-types.md` - Choosing appropriate data types
- `schema-partitioning.md` - Table partitioning strategies
- `lock-deadlock-prevention.md` - Avoiding deadlocks
- `lock-short-transactions.md` - Keep transactions short
- `monitor-explain-analyze.md` - Reading query plans
- `monitor-pg-stat-statements.md` - Tracking slow queries

## References

- https://www.postgresql.org/docs/current/
- https://supabase.com/docs
- https://wiki.postgresql.org/wiki/Performance_Optimization
- https://supabase.com/docs/guides/database/overview
- https://supabase.com/docs/guides/auth/row-level-security
