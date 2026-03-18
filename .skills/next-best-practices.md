---
name: next-best-practices
description: Next.js best practices - file conventions, RSC boundaries, data patterns, async APIs, metadata, error handling, route handlers, image/font optimization, bundling
user-invocable: false
source: https://github.com/vercel-labs/next-skills/tree/main/skills/next-best-practices
---

# Next.js Best Practices

Apply these rules when writing or reviewing Next.js code.

## File Conventions

- Project structure uses special files: `page.tsx`, `layout.tsx`, `loading.tsx`, `error.tsx`, `not-found.tsx`
- Route segments: dynamic `[slug]`, catch-all `[...slug]`, optional `[[...slug]]`, groups `(group)`
- Parallel routes with `@slot` and intercepting routes with `(.)`, `(..)`, `(...)` conventions
- Middleware rename in v16: `middleware` -> `proxy`

## RSC Boundaries

Detect invalid React Server Component patterns:
- **Async client components are invalid** - client components cannot be async
- **Non-serializable props** - functions, classes, Symbols cannot cross the RSC boundary
- **Exception**: Server Actions (functions with `'use server'`) CAN be passed as props

## Async Patterns (Next.js 15+)

- `params` and `searchParams` are now async - must `await` them
- `cookies()` and `headers()` are async - must `await` them
- Use the migration codemod: `npx @next/codemod@latest next-async-request-api .`

## Runtime Selection

- Default to **Node.js runtime** for all routes
- Use **Edge runtime** only when: latency-sensitive with simple logic, no Node.js APIs needed, small bundle required

## Directives

- `'use client'` - marks client component boundary (React directive)
- `'use server'` - marks Server Actions (React directive)
- `'use cache'` - marks cached content (Next.js directive, v16+)

## Functions

**Navigation hooks** (client-side):
- `useRouter` - programmatic navigation
- `usePathname` - current pathname
- `useSearchParams` - query string parameters (requires Suspense boundary)
- `useParams` - dynamic route parameters

**Server functions**:
- `cookies`, `headers` - read request data (async in v15+)
- `draftMode` - toggle draft mode
- `after` - schedule work after response sent
- `generateStaticParams` - define static paths
- `generateMetadata` - dynamic metadata generation

## Error Handling

- `error.tsx` - error boundary for route segment
- `global-error.tsx` - root error boundary
- `not-found.tsx` - 404 page
- `redirect()`, `permanentRedirect()` - navigation
- `notFound()` - trigger 404
- `forbidden()`, `unauthorized()` - auth errors
- `unstable_rethrow()` - re-throw in catch blocks

## Data Patterns

| Pattern | Use When |
|---------|----------|
| Server Components | Reading data during render |
| Server Actions | Mutations (forms, buttons) |
| Route Handlers | External API webhooks, non-React clients |

**Avoid data waterfalls**: Use `Promise.all()`, Suspense boundaries, and preload patterns.

**Client component data**: Use SWR or React Query for client-side fetching.

## Route Handlers

- Define in `route.ts` (not `route.tsx`)
- `GET` handler in same segment as `page.tsx` causes conflict
- No React DOM available in route handlers
- Prefer Server Actions over Route Handlers for mutations from React components

## Metadata & OG Images

- Static metadata: export `metadata` object from `layout.tsx` or `page.tsx`
- Dynamic metadata: export `generateMetadata` async function
- OG images: use `next/og` ImageResponse
- File-based: `opengraph-image.tsx`, `twitter-image.tsx`, `icon.tsx`

## Image Optimization

- **Always use `next/image`** over `<img>` tags
- Configure `remotePatterns` for external images
- Set responsive `sizes` attribute
- Use blur placeholders for better perceived performance
- Add `priority` to LCP (Largest Contentful Paint) images

## Font Optimization

- Use `next/font` for automatic font optimization
- Google Fonts: `import { Inter } from 'next/font/google'`
- Local fonts: `import localFont from 'next/font/local'`
- Integrate with Tailwind via CSS variable

## Bundling

- Use `serverExternalPackages` for server-incompatible packages
- Import CSS files, don't use `<link>` tags
- Polyfills are included automatically
- Analyze bundle: `@next/bundle-analyzer`

## Hydration Errors

Common causes:
- Browser APIs (`window`, `document`) used during SSR
- Date/time differences between server and client
- Invalid HTML nesting (`<div>` inside `<p>`)

Fix: Use `useEffect` for browser-only code, suppress with `suppressHydrationWarning`

## Suspense Boundaries

- `useSearchParams` requires wrapping in `<Suspense>`
- `usePathname` may need Suspense in some cases
- Wrap dynamic content that should stream

## Self-Hosting

- Use `output: 'standalone'` for Docker deployments
- Configure cache handlers for multi-instance ISR
- Image optimization works but needs `sharp` package

## Debug Tricks

- MCP endpoint for AI-assisted debugging
- `--debug-build-paths` to rebuild specific routes
