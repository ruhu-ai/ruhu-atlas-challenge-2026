"""API request/response schemas.

These are Pydantic models that define API contracts at the HTTP boundary.
Separate from domain models (which are business logic) and DB models
(which are persistence).

Pattern:
- Request schemas: what client sends (coercion OK here)
- Response schemas: what server returns (includes computed fields)
"""
