"""Tests for /api/labels and /api/labels/save endpoints."""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

# Ensure the api_server module sees a JWT_SECRET at import time.
os.environ.setdefault("JWT_SECRET", "test-secret-for-labels-endpoint")

import api_server  # noqa: E402
import jwt as _jwt  # noqa: E402


WORKSHEET_FIXTURE = {
    "generated_at": "2026-06-01T00:00:00+00:00",
    "window_size_days": 6,
    "instruments": [
        {
            "instrument": "BARC",
            "windows": [
                {
                    "window_id": "BARC_2026-05-01_2026-05-09",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-09",
                    "my_label": None,
                    "my_confidence": None,
                    "my_notes": None,
                },
                {
                    "window_id": "BARC_2026-05-09_2026-05-16",
                    "start_date": "2026-05-09",
                    "end_date": "2026-05-16",
                    "my_label": None,
                    "my_confidence": None,
                    "my_notes": None,
                },
            ],
        }
    ],
}


def _auth_header(secret: str = None) -> dict:
    secret = secret or api_server.JWT_SECRET
    token = _jwt.encode({"sub": "tester"}, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    labels_file = tmp_path / "regime_labels.json"
    labels_file.write_text(json.dumps(WORKSHEET_FIXTURE))
    monkeypatch.setattr(api_server, "LABELS_FILE", str(labels_file))
    api_server.app.config["TESTING"] = True
    return api_server.app.test_client(), labels_file


class TestLabelsSave:
    def test_valid_label_returns_200_and_persists(self, client):
        c, labels_file = client
        resp = c.post(
            "/api/labels/save",
            headers=_auth_header(),
            json={
                "window_id": "BARC_2026-05-01_2026-05-09",
                "my_label": "TRENDING",
                "my_confidence": 4,
                "my_notes": "clear uptrend",
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        # File on disk has the new values.
        on_disk = json.loads(labels_file.read_text())
        win = on_disk["instruments"][0]["windows"][0]
        assert win["my_label"] == "TRENDING"
        assert win["my_confidence"] == 4
        assert win["my_notes"] == "clear uptrend"
        # The other window is untouched.
        assert on_disk["instruments"][0]["windows"][1]["my_label"] is None

    def test_invalid_label_returns_400(self, client):
        c, _ = client
        resp = c.post(
            "/api/labels/save",
            headers=_auth_header(),
            json={
                "window_id": "BARC_2026-05-01_2026-05-09",
                "my_label": "MAYBE",
                "my_confidence": 3,
            },
        )
        assert resp.status_code == 400

    def test_invalid_confidence_returns_400(self, client):
        c, _ = client
        for bad in (0, 6, "high", 3.5):
            resp = c.post(
                "/api/labels/save",
                headers=_auth_header(),
                json={
                    "window_id": "BARC_2026-05-01_2026-05-09",
                    "my_label": "TRENDING",
                    "my_confidence": bad,
                },
            )
            assert resp.status_code == 400, f"confidence={bad!r}"

    def test_missing_jwt_returns_401(self, client):
        c, _ = client
        resp = c.post(
            "/api/labels/save",
            json={
                "window_id": "BARC_2026-05-01_2026-05-09",
                "my_label": "TRENDING",
                "my_confidence": 4,
            },
        )
        assert resp.status_code == 401

    def test_invalid_jwt_returns_401(self, client):
        c, _ = client
        resp = c.post(
            "/api/labels/save",
            headers=_auth_header("wrong-secret"),
            json={
                "window_id": "BARC_2026-05-01_2026-05-09",
                "my_label": "TRENDING",
                "my_confidence": 4,
            },
        )
        assert resp.status_code == 401

    def test_unknown_window_id_returns_404(self, client):
        c, _ = client
        resp = c.post(
            "/api/labels/save",
            headers=_auth_header(),
            json={
                "window_id": "FOO_2026-01-01_2026-01-08",
                "my_label": "TRENDING",
                "my_confidence": 4,
            },
        )
        assert resp.status_code == 404

    def test_notes_size_limit(self, client):
        c, _ = client
        resp = c.post(
            "/api/labels/save",
            headers=_auth_header(),
            json={
                "window_id": "BARC_2026-05-01_2026-05-09",
                "my_label": "TRENDING",
                "my_confidence": 4,
                "my_notes": "x" * (api_server.MAX_NOTES_LEN + 1),
            },
        )
        assert resp.status_code == 400

    def test_concurrent_saves_dont_corrupt_file(self, client):
        c, labels_file = client
        errors: list = []

        def save(window_id, label):
            try:
                resp = c.post(
                    "/api/labels/save",
                    headers=_auth_header(),
                    json={"window_id": window_id, "my_label": label,
                          "my_confidence": 3},
                )
                if resp.status_code != 200:
                    errors.append((window_id, resp.status_code,
                                   resp.get_data(as_text=True)))
            except Exception as e:
                errors.append((window_id, "exc", str(e)))

        threads = [
            threading.Thread(target=save,
                             args=("BARC_2026-05-01_2026-05-09", "TRENDING")),
            threading.Thread(target=save,
                             args=("BARC_2026-05-09_2026-05-16", "RANGING")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"errors during concurrent save: {errors}"
        # File is still valid JSON and both labels are present.
        on_disk = json.loads(labels_file.read_text())
        labels = {w["window_id"]: w["my_label"]
                  for w in on_disk["instruments"][0]["windows"]}
        assert labels["BARC_2026-05-01_2026-05-09"] == "TRENDING"
        assert labels["BARC_2026-05-09_2026-05-16"] == "RANGING"


class TestLabelsGet:
    def test_get_returns_worksheet(self, client):
        c, _ = client
        resp = c.get("/api/labels", headers=_auth_header())
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["window_size_days"] == 6
        assert len(body["instruments"]) == 1

    def test_get_requires_jwt(self, client):
        c, _ = client
        resp = c.get("/api/labels")
        assert resp.status_code == 401
