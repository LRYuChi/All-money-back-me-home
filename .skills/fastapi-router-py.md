---
name: fastapi-router-py
description: Create FastAPI routers with CRUD operations, authentication dependencies, and proper response models. Use when building REST API endpoints, creating new routes, implementing CRUD operations, or adding authenticated endpoints in FastAPI applications.
source: https://github.com/microsoft/skills/tree/main/.github/plugins/azure-sdk-python/skills/fastapi-router-py
---

# FastAPI Router

Create FastAPI routers following established patterns with proper authentication, response models, and HTTP status codes.

## Quick Start

Copy the template and replace placeholders:
- `{{ResourceName}}` -> PascalCase name (e.g., `Project`)
- `{{resource_name}}` -> snake_case name (e.g., `project`)
- `{{resource_plural}}` -> plural form (e.g., `projects`)

## Authentication Patterns

```python
# Optional auth - returns None if not authenticated
current_user: Optional[User] = Depends(get_current_user)

# Required auth - raises 401 if not authenticated
current_user: User = Depends(get_current_user_required)
```

## Response Models

```python
@router.get("/items/{item_id}", response_model=Item)
async def get_item(item_id: str) -> Item:
    ...

@router.get("/items", response_model=list[Item])
async def list_items() -> list[Item]:
    ...
```

## HTTP Status Codes

```python
@router.post("/items", status_code=status.HTTP_201_CREATED)
@router.delete("/items/{id}", status_code=status.HTTP_204_NO_CONTENT)
```

## Integration Steps

1. Create router in `src/backend/app/routers/`
2. Mount in `src/backend/app/main.py`
3. Create corresponding Pydantic models
4. Create service layer if needed
5. Add frontend API functions

---

## Router Template

```python
"""
{{ResourceName}} Router

Handles CRUD operations for {{resource_name}} resources.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.jwt import get_current_user, get_current_user_required
from app.models.user import User
from app.models.{{resource_name}} import (
    {{ResourceName}},
    {{ResourceName}}Create,
    {{ResourceName}}Update,
)
from app.services.{{resource_name}}_service import {{ResourceName}}Service

router = APIRouter(prefix="/api", tags=["{{resource_plural}}"])


def get_service() -> {{ResourceName}}Service:
    """Dependency to get service instance."""
    return {{ResourceName}}Service()


@router.get("/{{resource_plural}}", response_model=list[{{ResourceName}}])
async def list_{{resource_plural}}(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: Optional[User] = Depends(get_current_user),
    service: {{ResourceName}}Service = Depends(get_service),
) -> list[{{ResourceName}}]:
    """List all {{resource_plural}}."""
    return await service.list_{{resource_plural}}(limit=limit, offset=offset)


@router.get("/{{resource_plural}}/{{{resource_name}}_id}", response_model={{ResourceName}})
async def get_{{resource_name}}(
    {{resource_name}}_id: str,
    current_user: Optional[User] = Depends(get_current_user),
    service: {{ResourceName}}Service = Depends(get_service),
) -> {{ResourceName}}:
    """Get a specific {{resource_name}} by ID."""
    result = await service.get_{{resource_name}}_by_id({{resource_name}}_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="{{ResourceName}} not found",
        )
    return result


@router.post(
    "/{{resource_plural}}",
    response_model={{ResourceName}},
    status_code=status.HTTP_201_CREATED,
)
async def create_{{resource_name}}(
    data: {{ResourceName}}Create,
    current_user: User = Depends(get_current_user_required),
    service: {{ResourceName}}Service = Depends(get_service),
) -> {{ResourceName}}:
    """Create a new {{resource_name}}. Requires authentication."""
    return await service.create_{{resource_name}}(data, current_user.id)


@router.patch("/{{resource_plural}}/{{{resource_name}}_id}", response_model={{ResourceName}})
async def update_{{resource_name}}(
    {{resource_name}}_id: str,
    data: {{ResourceName}}Update,
    current_user: User = Depends(get_current_user_required),
    service: {{ResourceName}}Service = Depends(get_service),
) -> {{ResourceName}}:
    """Update an existing {{resource_name}}. Requires authentication."""
    existing = await service.get_{{resource_name}}_by_id({{resource_name}}_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="{{ResourceName}} not found",
        )
    return await service.update_{{resource_name}}({{resource_name}}_id, data)


@router.delete(
    "/{{resource_plural}}/{{{resource_name}}_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_{{resource_name}}(
    {{resource_name}}_id: str,
    current_user: User = Depends(get_current_user_required),
    service: {{ResourceName}}Service = Depends(get_service),
) -> None:
    """Delete a {{resource_name}}. Requires authentication."""
    existing = await service.get_{{resource_name}}_by_id({{resource_name}}_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="{{ResourceName}} not found",
        )
    await service.delete_{{resource_name}}({{resource_name}}_id)
```

---

## Acceptance Criteria

### Router Creation Patterns

**CORRECT: Basic Router Setup**
```python
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["items"])

@router.get("/items")
async def list_items() -> list[ItemResponse]:
    """List all items."""
    return []
```

**CORRECT: Router with Dependencies**
```python
router = APIRouter(prefix="/api", tags=["items"])

def get_service() -> ItemService:
    return ItemService()

@router.get("/items")
async def list_items(service: ItemService = Depends(get_service)):
    return await service.list_items()
```

**INCORRECT: Missing prefix or tags**
```python
router = APIRouter()  # No prefix, no tags
```

### HTTP Methods and Status Codes

**CORRECT:**
```python
# GET - implicit 200
@router.get("/items/{item_id}", response_model=ItemResponse)

# POST - 201 Created
@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)

# PATCH - 200 (partial update)
@router.patch("/items/{item_id}", response_model=ItemResponse)

# DELETE - 204 No Content
@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
```

### Error Handling

**CORRECT:**
```python
@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str) -> ItemResponse:
    item = await service.get_item(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item not found",
        )
    return item
```

### Authentication

**CORRECT: Required auth for write operations**
```python
@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def create_item(
    data: ItemCreate,
    current_user: User = Depends(get_current_user_required),
) -> ItemResponse:
    return await service.create_item(data, owner_id=current_user.id)
```

### Query Parameters with Validation

```python
@router.get("/items", response_model=list[ItemResponse])
async def list_items(
    limit: int = Query(default=50, ge=1, le=100, description="Max items to return"),
    offset: int = Query(default=0, ge=0, description="Items to skip"),
) -> list[ItemResponse]:
    return await service.list_items(limit=limit, offset=offset)
```

### Anti-Patterns Summary

| Anti-Pattern | Impact | Fix |
|--------------|--------|-----|
| Using `def` instead of `async def` | Blocks event loop | Change to `async def` |
| Missing `response_model` | Schema wrong, validation skipped | Add `response_model=YourModel` |
| Wrong status codes | Confuses clients | Use `status.HTTP_*` constants |
| Missing error handling | 500 errors | Use `HTTPException` |
| Auth on wrong endpoints | Security vulnerability | Required for writes, optional for reads |
| Service creation inline | Hard to test | Use `Depends(get_service)` |
| No parameter validation | Invalid data accepted | Use `Query()`, `Path()` with constraints |

### Complete Router Checklist

- [ ] Router has `prefix` and `tags`
- [ ] All endpoints are `async def`
- [ ] Each endpoint has `response_model` specified
- [ ] POST returns `status.HTTP_201_CREATED`
- [ ] DELETE returns `status.HTTP_204_NO_CONTENT`
- [ ] Authentication required for write operations
- [ ] Error handling with appropriate HTTPException status codes
- [ ] Query parameters have validation
- [ ] Path parameters are typed
- [ ] Service is injected via `Depends()`
- [ ] Docstrings on all endpoints
- [ ] No hardcoded values
