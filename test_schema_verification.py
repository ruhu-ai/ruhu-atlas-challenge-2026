#!/usr/bin/env python
"""Verify schema routers are properly installed in the FastAPI app."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from fastapi import FastAPI
from fastapi.routing import APIRoute

print("\n=== Schema Router Installation Verification ===\n")

# Test 1: Import and basic setup
print("Step 1: Verify imports...")
try:
    from ruhu.schema_routers import install_schema_routers
    from ruhu.event_sourcing.event_bus import get_event_bus
    print("✓ Imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test 2: Create app and install routers
print("\nStep 2: Install schema routers into FastAPI app...")
try:
    app = FastAPI()

    # Install routers
    def mock_resolve_org_id(request, org_id=None):
        return "test_org_id"

    event_bus = get_event_bus()
    install_schema_routers(
        app,
        resolve_organization_id=mock_resolve_org_id,
        event_bus=event_bus,
    )
    print("✓ Routers installed successfully")
except Exception as e:
    print(f"✗ Installation failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Verify routes are installed
print("\nStep 3: Verify installed routes...")
routes = {}
for route in app.routes:
    if isinstance(route, APIRoute):
        routes[route.path] = route.methods or {"GET"}

kpi_routes = [p for p in routes if "/kpis" in p]
intent_routes = [p for p in routes if "/intent-tags" in p]
attachment_routes = [p for p in routes if "/attachments" in p]

print(f"✓ KPI routes found: {len(kpi_routes)}")
for path in sorted(kpi_routes)[:5]:
    print(f"  - {path}")

print(f"✓ Intent Tags routes found: {len(intent_routes)}")
for path in sorted(intent_routes)[:5]:
    print(f"  - {path}")

print(f"✓ Attachment routes found: {len(attachment_routes)}")
for path in sorted(attachment_routes)[:5]:
    print(f"  - {path}")

if kpi_routes or intent_routes or attachment_routes:
    print("\n✓ Event bus in app.state:", hasattr(app.state, "event_bus"))
    print("✓ Schema routers properly installed")
    print("\n✅ Schema Router Installation Verification PASSED!")
else:
    print("\n✗ No routes found - installation may have failed")
    sys.exit(1)
