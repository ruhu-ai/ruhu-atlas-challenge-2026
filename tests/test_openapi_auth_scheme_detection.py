"""Tier 3: OpenAPI auth-scheme detection.

Customers upload an OpenAPI spec and expect the platform to figure out
how to authenticate against the API. ``detect_auth_schemes`` parses
``components.securitySchemes`` (OpenAPI 3.x) or ``securityDefinitions``
(OpenAPI 2.0) and returns ``DetectedAuthScheme`` records mapped to our
``APIConnectionRecord.auth_type`` vocabulary, so the combined create+
ingest flow can pre-populate connection records without re-mapping.

These tests pin:

* Each scheme type maps to the correct ``auth_type``.
* OpenAPI 2.0 ``securityDefinitions`` is also parsed (still common in
  legacy customer Swagger files).
* OAuth2 multi-flow specs prefer authorization_code over the
  alternatives (matches what most SaaS providers use).
* Unknown / vendor scheme types are skipped silently rather than
  blowing up the whole ingestion.
* The detection is wired into the ``ingest()`` result so downstream
  callers don't need to re-parse.
"""
from __future__ import annotations

from ruhu.tools.ingestion import (
    DetectedAuthScheme,
    detect_auth_schemes,
)


# ── Empty / public APIs ─────────────────────────────────────────────────


def test_no_schemes_returns_empty_list_for_public_api() -> None:
    assert detect_auth_schemes({"openapi": "3.0.0"}) == []


def test_invalid_input_returns_empty_list() -> None:
    assert detect_auth_schemes({}) == []
    assert detect_auth_schemes({"components": "not a dict"}) == []  # type: ignore[arg-type]


# ── HTTP bearer / basic ─────────────────────────────────────────────────


def test_detects_bearer_token_scheme() -> None:
    spec = {
        "openapi": "3.0.0",
        "components": {
            "securitySchemes": {
                "BearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Pass a bearer token from the dashboard.",
                }
            }
        },
    }
    detected = detect_auth_schemes(spec)
    assert detected == [
        DetectedAuthScheme(
            name="BearerAuth",
            auth_type="bearer_token",
            description="Pass a bearer token from the dashboard.",
        )
    ]


def test_detects_basic_auth_scheme_in_openapi_3() -> None:
    spec = {
        "openapi": "3.0.0",
        "components": {
            "securitySchemes": {
                "BasicAuth": {"type": "http", "scheme": "basic"}
            }
        },
    }
    detected = detect_auth_schemes(spec)
    assert len(detected) == 1
    assert detected[0].auth_type == "basic"


def test_skips_unsupported_http_schemes_like_digest() -> None:
    """Digest, HOBA, and other RFC 7235 schemes aren't connection
    auth_types we support — better to skip than to mis-route them."""
    spec = {
        "components": {
            "securitySchemes": {
                "DigestAuth": {"type": "http", "scheme": "digest"}
            }
        }
    }
    assert detect_auth_schemes(spec) == []


# ── API key (header / query / cookie) ───────────────────────────────────


def test_detects_api_key_in_header() -> None:
    spec = {
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                }
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert len(detected) == 1
    scheme = detected[0]
    assert scheme.auth_type == "api_key"
    assert scheme.api_key_location == "header"
    assert scheme.api_key_name == "X-API-Key"


def test_detects_api_key_in_query() -> None:
    spec = {
        "components": {
            "securitySchemes": {
                "QueryKey": {"type": "apiKey", "in": "query", "name": "api_key"}
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert detected[0].api_key_location == "query"
    assert detected[0].api_key_name == "api_key"


# ── OAuth2 ──────────────────────────────────────────────────────────────


def test_detects_oauth2_authorization_code_flow() -> None:
    spec = {
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://example.com/oauth/authorize",
                            "tokenUrl": "https://example.com/oauth/token",
                            "refreshUrl": "https://example.com/oauth/refresh",
                            "scopes": {
                                "read": "Read access",
                                "write": "Write access",
                            },
                        }
                    },
                }
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert len(detected) == 1
    scheme = detected[0]
    assert scheme.auth_type == "oauth2"
    assert scheme.oauth_flow == "authorization_code"
    assert scheme.authorization_url == "https://example.com/oauth/authorize"
    assert scheme.token_url == "https://example.com/oauth/token"
    assert scheme.refresh_url == "https://example.com/oauth/refresh"
    assert sorted(scheme.scopes) == ["read", "write"]


def test_oauth2_prefers_authorization_code_over_other_flows() -> None:
    """When a spec advertises multiple flows (common with Google,
    Microsoft Graph), authorization_code is picked because that's the
    flow the platform's OAuth machinery implements."""
    spec = {
        "components": {
            "securitySchemes": {
                "Multi": {
                    "type": "oauth2",
                    "flows": {
                        "implicit": {
                            "authorizationUrl": "https://example.com/implicit",
                            "scopes": {"read": ""},
                        },
                        "authorizationCode": {
                            "authorizationUrl": "https://example.com/authcode",
                            "tokenUrl": "https://example.com/token",
                            "scopes": {"read": "", "write": ""},
                        },
                        "clientCredentials": {
                            "tokenUrl": "https://example.com/cc",
                            "scopes": {},
                        },
                    },
                }
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert detected[0].oauth_flow == "authorization_code"
    assert detected[0].authorization_url == "https://example.com/authcode"


def test_oauth2_falls_back_to_client_credentials_when_no_authcode() -> None:
    spec = {
        "components": {
            "securitySchemes": {
                "ServerToServer": {
                    "type": "oauth2",
                    "flows": {
                        "clientCredentials": {
                            "tokenUrl": "https://example.com/oauth/token",
                            "scopes": {"api": ""},
                        }
                    },
                }
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert detected[0].oauth_flow == "client_credentials"
    assert detected[0].token_url == "https://example.com/oauth/token"


# ── OpenID Connect ──────────────────────────────────────────────────────


def test_detects_openid_connect_url() -> None:
    spec = {
        "components": {
            "securitySchemes": {
                "OIDC": {
                    "type": "openIdConnect",
                    "openIdConnectUrl": "https://example.com/.well-known/openid-configuration",
                }
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert len(detected) == 1
    assert detected[0].auth_type == "openid_connect"
    assert (
        detected[0].openid_connect_url
        == "https://example.com/.well-known/openid-configuration"
    )


# ── OpenAPI 2.0 (Swagger) backward compat ───────────────────────────────


def test_detects_swagger_2_security_definitions() -> None:
    """Customers still upload Swagger 2.0 specs (Twilio, AWS legacy).
    The same parser must handle ``securityDefinitions`` at the root."""
    spec = {
        "swagger": "2.0",
        "securityDefinitions": {
            "BasicAuth": {"type": "basic"},
            "ApiKey": {"type": "apiKey", "in": "header", "name": "Authorization"},
        },
    }
    detected = detect_auth_schemes(spec)
    auth_types = {s.auth_type for s in detected}
    assert auth_types == {"basic", "api_key"}


def test_detects_swagger_2_oauth2_single_flow_shape() -> None:
    """Swagger 2.0 used a single-flow form (``flow``, ``authorizationUrl``,
    ``tokenUrl`` at the scheme root) instead of the OpenAPI 3 ``flows`` map."""
    spec = {
        "swagger": "2.0",
        "securityDefinitions": {
            "OAuth2": {
                "type": "oauth2",
                "flow": "accessCode",  # Swagger 2's name for authorization_code
                "authorizationUrl": "https://example.com/authorize",
                "tokenUrl": "https://example.com/token",
                "scopes": {"read": "Read", "write": "Write"},
            }
        },
    }
    detected = detect_auth_schemes(spec)
    assert len(detected) == 1
    scheme = detected[0]
    assert scheme.auth_type == "oauth2"
    assert scheme.oauth_flow == "authorization_code"  # accessCode → authorization_code
    assert scheme.authorization_url == "https://example.com/authorize"
    assert sorted(scheme.scopes) == ["read", "write"]


# ── Multiple schemes ────────────────────────────────────────────────────


def test_detects_multiple_schemes_preserving_order() -> None:
    """Spec authors put preferred schemes first; preserve that ordering
    so the UI can use the first scheme as the default suggestion."""
    spec = {
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://x/auth",
                            "tokenUrl": "https://x/token",
                            "scopes": {},
                        }
                    },
                },
                "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
                "Bearer": {"type": "http", "scheme": "bearer"},
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert [s.name for s in detected] == ["OAuth2", "ApiKey", "Bearer"]
    assert [s.auth_type for s in detected] == ["oauth2", "api_key", "bearer_token"]


def test_skips_unknown_scheme_type_silently() -> None:
    """A vendor extension or future scheme type must not break detection
    of the other schemes in the same document."""
    spec = {
        "components": {
            "securitySchemes": {
                "Unknown": {"type": "magicSauce", "secret": "..."},
                "Bearer": {"type": "http", "scheme": "bearer"},
            }
        }
    }
    detected = detect_auth_schemes(spec)
    assert [s.name for s in detected] == ["Bearer"]


# ── Wired into ingest() result ──────────────────────────────────────────


def test_ingestion_result_includes_detected_auth_schemes(
    postgres_database_url_factory, credential_cipher
) -> None:
    """End-to-end: an ingest call returns detected_auth_schemes alongside
    the created tools — proves the parsing runs in the actual ingestion
    path, not just as an isolated helper."""
    from ruhu.db import build_session_factory
    from ruhu.tools.ingestion import OpenAPIToolIngestionService
    from ruhu.tools.management import (
        APIConnectionStore,
        ToolDefinitionStore,
    )

    sf = build_session_factory(postgres_database_url_factory())
    connection_store = APIConnectionStore(sf, blob_cipher=credential_cipher)
    definition_store = ToolDefinitionStore(sf)
    service = OpenAPIToolIngestionService(
        connection_store=connection_store,
        definition_store=definition_store,
    )

    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Demo", "version": "1.0"},
        "servers": [{"url": "https://demo.example.com"}],
        "components": {
            "securitySchemes": {
                "OAuth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": "https://demo.example.com/auth",
                            "tokenUrl": "https://demo.example.com/token",
                            "scopes": {"read": "Read access"},
                        }
                    },
                }
            }
        },
        "paths": {
            "/widgets": {
                "get": {
                    "operationId": "listWidgets",
                    "summary": "List widgets",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    result = service.ingest(
        organization_id="org-A",
        spec=spec,
        display_name="demo",
        provider="demo",
        auth_type="oauth2",
        base_url="https://demo.example.com",
    )
    assert len(result.created_tool_ids) == 1
    assert len(result.detected_auth_schemes) == 1
    detected = result.detected_auth_schemes[0]
    assert detected.auth_type == "oauth2"
    assert detected.authorization_url == "https://demo.example.com/auth"
    assert detected.scopes == ["read"]
