"""
tests/test_health.py
─────────────────────
Integration tests for the FastAPI health endpoint.

Run with:
    uv run pytest tests/test_health.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_root_returns_200():
    response = client.get("/")
    assert response.status_code == 200
    
    
def test_health_endpoint_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_schema():
    response = client.get("/health")
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "environment" in data
    assert "llm_providers" in data


def test_health_llm_providers_structure():
    response = client.get("/health")
    data = response.json()
    providers = data["llm_providers"]
    assert "gemini" in providers
    assert "groq" in providers
    assert isinstance(providers["gemini"], bool)
    assert isinstance(providers["groq"], bool)


def test_docs_endpoint_available():
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_available():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Fraud AI Investigator"
