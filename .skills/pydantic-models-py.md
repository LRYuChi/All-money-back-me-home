---
name: pydantic-models-py
description: Create Pydantic models following the multi-model pattern with Base, Create, Update, Response, and InDB variants. Use when defining API request/response schemas, database models, or data validation in Python applications using Pydantic v2.
source: https://github.com/microsoft/skills/tree/main/.github/plugins/azure-sdk-python/skills/pydantic-models-py
---

# Pydantic Models

Create Pydantic models following the multi-model pattern for clean API contracts.

## Quick Start

Copy the template and replace placeholders:
- `{{ResourceName}}` -> PascalCase name (e.g., `Project`)
- `{{resource_name}}` -> snake_case name (e.g., `project`)

## Multi-Model Pattern

| Model | Purpose |
|-------|---------|
| `Base` | Common fields shared across models |
| `Create` | Request body for creation (required fields) |
| `Update` | Request body for updates (all optional) |
| `Response` | API response with all fields |
| `InDB` | Database document with `doc_type` |

## camelCase Aliases

```python
class MyModel(BaseModel):
    workspace_id: str = Field(..., alias="workspaceId")
    created_at: datetime = Field(..., alias="createdAt")

    class Config:
        populate_by_name = True  # Accept both snake_case and camelCase
```

## Optional Update Fields

```python
class MyUpdate(BaseModel):
    """All fields optional for PATCH requests."""
    name: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
```

## Database Document

```python
class MyInDB(MyResponse):
    """Adds doc_type for database queries."""
    doc_type: str = "my_resource"
```

## Integration Steps

1. Create models in `src/backend/app/models/`
2. Export from `src/backend/app/models/__init__.py`
3. Add corresponding TypeScript types

---

## Model Template

```python
"""
{{ResourceName}} Models

Pydantic models for {{resource_name}} resource following the multi-model pattern.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class {{ResourceName}}Base(BaseModel):
    """Base model with common fields."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Display name for the {{resource_name}}",
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Optional description",
    )

    class Config:
        populate_by_name = True


class {{ResourceName}}Create({{ResourceName}}Base):
    """Request model for creating a new {{resource_name}}."""

    workspace_id: str = Field(
        ...,
        alias="workspaceId",
        description="ID of the parent workspace",
    )


class {{ResourceName}}Update(BaseModel):
    """Request model for partial updates. All fields optional."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)

    class Config:
        populate_by_name = True


class {{ResourceName}}({{ResourceName}}Base):
    """Response model with all fields."""

    id: str = Field(..., description="Unique identifier")
    slug: str = Field(..., description="URL-friendly identifier")
    workspace_id: str = Field(..., alias="workspaceId")
    author_id: str = Field(..., alias="authorId")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")

    class Config:
        from_attributes = True
        populate_by_name = True


class {{ResourceName}}InDB({{ResourceName}}):
    """Database document model."""

    doc_type: str = "{{resource_name}}"
```

---

## Acceptance Criteria

### Import Patterns

**CORRECT:**
```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
```

**INCORRECT (v1 pattern):**
```python
class MyModel(BaseModel):
    class Config:
        orm_mode = True  # v1 - should use from_attributes = True
```

### Base Model Pattern

**CORRECT:**
```python
class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)

    class Config:
        populate_by_name = True
```

**CORRECT with ConfigDict (v2):**
```python
from pydantic import BaseModel, ConfigDict

class ProjectBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(...)
```

### Create Model

**CORRECT:**
```python
class ProjectCreate(ProjectBase):
    workspace_id: str = Field(..., alias="workspaceId")
```

**INCORRECT:**
```python
class ProjectCreate(ProjectBase):
    workspace_id: Optional[str] = Field(...)  # Contradictory
```

### Update Model

**CORRECT: All fields optional**
```python
class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)

    class Config:
        populate_by_name = True
```

**INCORRECT:**
```python
class ProjectUpdate(BaseModel):
    name: str = Field(...)  # Should be Optional[str]
```

### Response Model

**CORRECT:**
```python
class Project(ProjectBase):
    id: str = Field(..., description="Unique identifier")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")

    class Config:
        from_attributes = True
        populate_by_name = True
```

### InDB Model

**CORRECT:**
```python
class ProjectInDB(Project):
    doc_type: str = "project"
```

**INCORRECT:**
```python
class ProjectInDB(Project):
    doc_type: Optional[str] = None  # Should be constant string
```

### Field Validation

```python
class Project(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")
    age: int = Field(..., ge=0, le=150)
    tags: list[str] = Field(default_factory=list, min_items=0, max_items=10)
    priority: int = Field(default=1, ge=1, le=5)
```

### Custom Validators (v2)

```python
from pydantic import field_validator

class Project(BaseModel):
    name: str

    @field_validator('name')
    @classmethod
    def name_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Name cannot be empty')
        return v.strip()
```

### Model Validators

```python
from pydantic import model_validator

class Project(BaseModel):
    start_date: datetime
    end_date: datetime

    @model_validator(mode='after')
    def validate_dates(self):
        if self.start_date >= self.end_date:
            raise ValueError('start_date must be before end_date')
        return self
```

### Common Mistakes and Fixes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Missing Field import | `NameError` | `from pydantic import BaseModel, Field` |
| Optional without default | `ValidationError: field required` | `field: Optional[str] = None` |
| Wrong alias config | camelCase not accepted | Add `populate_by_name = True` |
| Using v1 syntax | `ImportError, TypeError` | Use `@field_validator`, `from_attributes` |
| `doc_type` optional | Query failures | Use `doc_type: str = "resource_name"` |
| Not inheriting Base | Code duplication | `class Create(Base):` |
| All fields optional in Create | Missing validation | Inherit required Base fields |
| Response inherits Create | Update fields exposed | Inherit Base, not Create |

### SDK Version Info

- **Pydantic**: v2.x (current)
- **Python**: 3.9+
- **Key Breaking Changes from v1**:
  - `from pydantic import validator` -> `from pydantic import field_validator`
  - `Config.orm_mode` -> `Config.from_attributes`
  - `Config.json_schema_extra` replaces `schema_extra`
  - Validators require `@classmethod`
  - Type hints are mandatory
