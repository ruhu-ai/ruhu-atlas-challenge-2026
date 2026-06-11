"""Service layer extracted from the api.py monolith (RP-3.1, in progress).

Route handlers stay thin; projection/business logic lives here. New endpoint
logic must be added to a module in this package, never inline in api.py — the
line-budget ratchet enforces that api.py only shrinks.
"""
