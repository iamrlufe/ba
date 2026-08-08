"""Global exception handling.

SQLite/SQLAlchemy raise a bare `sqlalchemy.exc.IntegrityError` for unique
constraints, foreign key violations, and CHECK constraint failures alike --
there is no separate exception type per case. This handler inspects
`str(exc.orig)` (the raw DBAPI error message, e.g.
"UNIQUE constraint failed: servers.name") to produce a more specific
`detail`, and always maps to HTTP 409 Conflict, which is registered in
`app.main` via `app.add_exception_handler(IntegrityError, integrity_error_handler)`.
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    orig_message = str(exc.orig) if exc.orig is not None else str(exc)

    if "UNIQUE constraint failed" in orig_message:
        detail = f"Conflicts with an existing resource ({orig_message})"
    elif "FOREIGN KEY constraint failed" in orig_message:
        detail = (
            "Operation violates a foreign key relationship (referenced row "
            "is missing, or this row is still referenced by other rows)"
        )
    elif "CHECK constraint failed" in orig_message:
        detail = f"Violates a data constraint ({orig_message})"
    else:
        detail = f"Database integrity error ({orig_message})"

    return JSONResponse(status_code=409, content={"detail": detail})
