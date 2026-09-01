import json

import database


def _use_temporary_database(tmp_path, monkeypatch):
    path = tmp_path / "data" / "repolens.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(path))
    monkeypatch.setenv("ENABLE_DEMO_DATA", "false")
    database.init_db()
    return path


def _save(name="owner/repository", slug="owner-repository"):
    return database.save_analysis(
        slug=slug,
        repo_url=f"https://github.com/{name}",
        repo_name=name,
        input_mode="url",
        raw_commits=[],
        grouped_commits=[],
        commit_count=0,
    )


def test_database_crud_and_pagination(tmp_path, monkeypatch):
    _use_temporary_database(tmp_path, monkeypatch)
    analysis_id = _save()

    database.update_progress(analysis_id, 150, "Indexing")
    database.save_extended_data(
        analysis_id,
        {"technologies": {"technologies": [{"name": "Python"}]}},
    )
    database.update_narratives(
        analysis_id,
        {
            "release": "Release",
            "standup": "Standup",
            "onboarding": "Onboarding",
            "portfolio": "Portfolio",
        },
    )

    analysis = database.get_analysis_by_id(analysis_id)
    by_slug = database.get_analysis_by_slug("owner-repository")
    extended = database.get_extended_data(analysis_id)
    rows, total = database.get_all_analyses(search="repository", page=1, per_page=5)

    assert analysis["status"] == "done"
    assert analysis["progress"] == 100
    assert by_slug["id"] == analysis_id
    assert extended["technologies"]["technologies"][0]["name"] == "Python"
    assert total == 1
    assert rows[0]["id"] == analysis_id


def test_database_error_and_invalid_extended_data(tmp_path, monkeypatch):
    _use_temporary_database(tmp_path, monkeypatch)
    analysis_id = _save(slug="failed-analysis")

    database.set_error(analysis_id, "Network unavailable")
    with database.get_db() as connection:
        connection.execute(
            "UPDATE analyses SET extended_data_json=? WHERE id=?",
            ("not-json", analysis_id),
        )

    analysis = database.get_analysis_by_id(analysis_id)

    assert analysis["status"] == "error"
    assert analysis["error_message"] == "Network unavailable"
    assert database.get_extended_data(analysis_id) == {}
    assert database.get_analysis_by_id(99999) is None


def test_stale_analysis_recovery_only_changes_old_jobs(tmp_path, monkeypatch):
    _use_temporary_database(tmp_path, monkeypatch)
    old_id = _save(name="owner/old", slug="old")
    new_id = _save(name="owner/new", slug="new")

    with database.get_db() as connection:
        connection.execute(
            "UPDATE analyses SET created_at=datetime('now', '-30 minutes') WHERE id=?",
            (old_id,),
        )

    recovered = database.recover_stale_analyses(minutes=10)

    assert [row["id"] for row in recovered] == [old_id]
    recovered_analysis = database.get_analysis_by_id(old_id)
    assert recovered_analysis["status"] == "error"
    assert recovered_analysis["progress"] == 100
    assert recovered_analysis["stage"] == "Failed"
    assert "Please retry" in recovered_analysis["error_message"]
    assert database.get_analysis_by_id(new_id)["status"] == "pending"


def test_empty_database_lists_and_json_storage(tmp_path, monkeypatch):
    _use_temporary_database(tmp_path, monkeypatch)

    rows, total = database.get_all_analyses()
    assert rows == []
    assert total == 0

    analysis_id = database.save_analysis(
        slug="pasted",
        repo_url="pasted:raw",
        repo_name="Pasted Repository",
        input_mode="paste",
        raw_commits=[{"message": "feat: test"}],
        grouped_commits=[{"label": "Week"}],
        commit_count=1,
    )
    analysis = database.get_analysis_by_id(analysis_id)

    assert json.loads(analysis["raw_commits_json"])[0]["message"] == "feat: test"
    assert json.loads(analysis["grouped_commits_json"])[0]["label"] == "Week"
