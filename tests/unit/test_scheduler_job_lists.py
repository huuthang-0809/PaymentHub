from fastapi.testclient import TestClient

from bootstrap import scheduler_api


def test_job_lists_are_separate_and_newest_first(monkeypatch):
    monkeypatch.setattr(scheduler_api, "run_generation", lambda _: None)
    monkeypatch.setattr(scheduler_api, "run_simulation", lambda _: None)
    with TestClient(scheduler_api.create_app()) as client:
        assert client.get("/generation-jobs").json() == []
        assert client.get("/simulation-jobs").json() == []
        first = client.post("/generation-jobs", json={"start_date": "2026-10-01"}).json()
        second = client.post("/generation-jobs", json={"start_date": "2026-10-02"}).json()
        simulation = client.post("/simulation-jobs", json={"start_date": "2026-10-01"}).json()
        generated = client.get("/generation-jobs").json()
        assert [job["job_id"] for job in generated] == [second["job_id"], first["job_id"]]
        assert all(job["status"] == "completed" for job in generated)
        assert [job["job_id"] for job in client.get("/simulation-jobs").json()] == [simulation["job_id"]]
