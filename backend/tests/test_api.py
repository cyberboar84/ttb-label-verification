"""API-level integration tests. Run the whole app in mock mode (no Azure) via
FastAPI's TestClient, exercising the real request/response path."""

import io
import json

import os
os.environ["MOCK_VISION"] = "true"

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _img_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (300, 400), "white").save(buf, "JPEG")
    return buf.getvalue()


def test_health_reports_mock():
    j = client.get("/health").json()
    assert j["status"] == "ok"
    assert j["mode"] == "mock"


def test_verify_single_pass():
    app_data = {"brand_name": "Old Tom Distillery",
                "class_type": "Kentucky Straight Bourbon Whiskey",
                "alcohol_content": "45% Alc./Vol.", "net_contents": "750 mL"}
    r = client.post("/api/verify",
                    files={"image": ("old_tom.jpg", _img_bytes(), "image/jpeg")},
                    data={"application": json.dumps(app_data)})
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] == "PASS"
    assert body["processing_ms"] is not None


def test_verify_single_abv_mismatch():
    app_data = {"brand_name": "Old Tom Distillery", "alcohol_content": "40% Alc./Vol."}
    r = client.post("/api/verify",
                    files={"image": ("old_tom.jpg", _img_bytes(), "image/jpeg")},
                    data={"application": json.dumps(app_data)})
    assert r.json()["overall"] == "MISMATCH"


def test_verify_no_application_runs_compliance():
    # No application field at all -> zero-input compliance mode.
    r = client.post("/api/verify",
                    files={"image": ("old_tom.jpg", _img_bytes(), "image/jpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "compliance"
    assert body["overall"] == "PASS"  # mock label has all elements + good warning


def test_verify_multi_panel():
    # Front + back of one bottle, sent as multiple panels under `images`.
    files = [("images", ("front.jpg", _img_bytes(), "image/jpeg")),
             ("images", ("back.jpg", _img_bytes(), "image/jpeg"))]
    r = client.post("/api/verify", files=files)
    assert r.status_code == 200
    assert r.json()["overall"] == "PASS"  # mock returns full fields per panel


def test_verify_rejects_non_image():
    r = client.post("/api/verify",
                    files={"image": ("notes.txt", b"hello", "text/plain")},
                    data={"application": "{}"})
    assert r.status_code == 415


def test_verify_rejects_bad_json():
    r = client.post("/api/verify",
                    files={"image": ("x.jpg", _img_bytes(), "image/jpeg")},
                    data={"application": "{not json"})
    assert r.status_code == 422


def test_batch():
    manifest = {"a.jpg": {"brand_name": "Old Tom Distillery"},
                "b.jpg": {"brand_name": "Old Tom Distillery"}}
    files = [("images", ("a.jpg", _img_bytes(), "image/jpeg")),
             ("images", ("b.jpg", _img_bytes(), "image/jpeg"))]
    r = client.post("/api/verify/batch", files=files,
                    data={"manifest": json.dumps(manifest)})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total"] == 2
    assert len(body["results"]) == 2
