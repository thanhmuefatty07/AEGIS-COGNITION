import json
from pathlib import Path

import pytest

from core.python.aegis.project_registry import (
    PROJECT_REGISTRY_SCHEMA_V1,
    ProjectRegistry,
    ProjectRegistryError,
    project_id_for_path,
)


def test_registry_round_trips_metadata_atomically_without_persisting_status(tmp_path: Path):
    registry_path = tmp_path / "profile" / "projects.registry.json"
    project = tmp_path / "workspace"
    project.mkdir()
    registry = ProjectRegistry(registry_path)

    first = registry.upsert(project, opened_at_ms=10)
    second = registry.upsert(project, opened_at_ms=20)

    assert first.project_id == second.project_id == project_id_for_path(project)
    assert second.open_count == 2
    assert registry.list() == (second,)
    persisted = json.loads(registry_path.read_text(encoding="utf-8"))
    assert persisted["schema"] == PROJECT_REGISTRY_SCHEMA_V1
    assert "status" not in persisted["projects"][0]
    assert persisted["projects"][0]["path"] == str(project.resolve())


def test_registry_reports_missing_projects_but_remove_only_unregisters_metadata(tmp_path: Path):
    registry = ProjectRegistry(tmp_path / "registry.json")
    project = tmp_path / "workspace"
    project.mkdir()
    record = registry.upsert(project, opened_at_ms=1)
    project.rmdir()

    assert registry.list()[0].as_dict()["status"] == "MISSING"
    assert registry.remove(record.project_id) is True
    assert registry.list() == ()
    assert not project.exists()
    assert registry.remove(record.project_id) is False


def test_registry_rejects_corrupt_identity_and_noncanonical_paths(tmp_path: Path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema": PROJECT_REGISTRY_SCHEMA_V1,
                "projects": [
                    {
                        "project_id": "project:" + "0" * 32,
                        "path": str(tmp_path / "missing"),
                        "name": "missing",
                        "parent_path": str(tmp_path),
                        "last_opened_at_ms": 1,
                        "open_count": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ProjectRegistryError, match="identity"):
        ProjectRegistry(registry_path).list()


def test_registry_is_bounded_and_rejects_invalid_remove_ids(tmp_path: Path):
    registry = ProjectRegistry(tmp_path / "registry.json", max_projects=1)
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    registry.upsert(one, opened_at_ms=1)
    registry.upsert(two, opened_at_ms=2)

    assert [item.path for item in registry.list()] == [str(two.resolve())]
    with pytest.raises(ProjectRegistryError, match="project_id"):
        registry.remove("not-a-project")
