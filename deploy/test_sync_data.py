import io
import json
import tarfile

import pytest

import sync_data
from sync_data import (
    SyncError,
    find_target,
    http_over_bash,
    latest_parsed_files,
    parse_http_response,
    remote_argv,
    write_tar,
)


def _container(cid, project, service, mounts=(), networks=("net",), labels=None):
    return {
        "Id": cid,
        "Name": f"/{service}-{cid}",
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                **(labels or {}),
            }
        },
        "Mounts": list(mounts),
        "NetworkSettings": {"Networks": {n: {} for n in networks}},
    }


PARSED_VOLUME = {"Type": "volume", "Name": "abc123_parsed-data", "Destination": "/app/data/parsed"}
PARSED_BIND = {"Type": "bind", "Source": "/home/x/parsed", "Destination": "/app/data/parsed"}


# --- latest_parsed_files ---------------------------------------------------


def test_latest_parsed_files_picks_newest_cards_and_rules(tmp_path):
    for name in [
        "cards_2026-08-25.jsonl",
        "cards_2026-09-26.jsonl",
        "rules_2026-08-25.jsonl",
        "rulings_2026-09-26.jsonl",
    ]:
        (tmp_path / name).write_text("{}\n")
    assert [p.name for p in latest_parsed_files(tmp_path)] == [
        "cards_2026-09-26.jsonl",
        "rules_2026-08-25.jsonl",
    ]


def test_latest_parsed_files_fails_when_a_kind_is_missing(tmp_path):
    (tmp_path / "cards_2026-09-26.jsonl").write_text("{}\n")
    with pytest.raises(SyncError, match="rules_"):
        latest_parsed_files(tmp_path)


# --- find_target -------------------------------------------------------------


def test_find_target_uses_the_container_with_the_parsed_volume():
    containers = [
        _container("b1", "abc123", "backend", mounts=[PARSED_VOLUME]),
        _container("q1", "abc123", "qdrant"),
        _container("q2", "other", "qdrant"),
    ]
    target = find_target(containers)
    assert target.backend == "b1"
    assert target.qdrant == "q1"
    assert target.parsed_volume == "abc123_parsed-data"


def test_find_target_lists_every_container_in_the_stack():
    # On a first deploy the frontend never starts (it waits for a healthy
    # backend), so sync starts the whole stack once the data is in.
    containers = [
        _container("b1", "abc123", "backend", mounts=[PARSED_VOLUME]),
        _container("q1", "abc123", "qdrant"),
        _container("f1", "abc123", "frontend"),
        _container("x1", "other", "frontend"),
    ]
    assert find_target(containers).containers == ("b1", "q1", "f1")


def test_find_target_ignores_bind_mounted_parsed_dirs():
    # The dev stack bind-mounts the parsed dir; only the prod stack uses a volume.
    containers = [
        _container("dev", "mtg-rules", "backend", mounts=[PARSED_BIND]),
        _container("b1", "abc123", "backend", mounts=[PARSED_VOLUME]),
        _container("q1", "abc123", "qdrant"),
    ]
    assert find_target(containers).backend == "b1"


def test_find_target_fails_when_no_stack_is_deployed():
    with pytest.raises(SyncError, match="no backend container"):
        find_target([_container("q1", "abc123", "qdrant")])


def test_find_target_fails_when_the_stack_has_no_qdrant():
    with pytest.raises(SyncError, match="no qdrant container"):
        find_target([_container("b1", "abc123", "backend", mounts=[PARSED_VOLUME])])


def test_find_target_asks_for_a_project_when_several_stacks_match():
    containers = [
        _container("b1", "abc123", "backend", mounts=[PARSED_VOLUME]),
        _container("b2", "def456", "backend", mounts=[PARSED_VOLUME]),
    ]
    with pytest.raises(SyncError, match="--project") as exc:
        find_target(containers)
    assert "abc123" in str(exc.value) and "def456" in str(exc.value)


def test_find_target_project_picks_one_of_several_stacks():
    containers = [
        _container("b1", "abc123", "backend", mounts=[PARSED_VOLUME]),
        _container("q1", "abc123", "qdrant"),
        _container("b2", "def456", "backend", mounts=[PARSED_VOLUME]),
        _container("q2", "def456", "qdrant"),
    ]
    target = find_target(containers, project="def456")
    assert (target.backend, target.qdrant) == ("b2", "q2")


# --- remote_argv -------------------------------------------------------------


def test_remote_argv_quotes_the_command_for_the_remote_shell():
    argv = remote_argv("root@example.com", ["docker", "exec", "c1", "bash", "-c", "a b; c"])
    assert argv == ["ssh", "root@example.com", "docker exec c1 bash -c 'a b; c'"]


def test_remote_argv_without_a_host_runs_locally():
    assert remote_argv(None, ["docker", "ps"]) == ["docker", "ps"]


# --- http_over_bash / parse_http_response -----------------------------------


def test_http_over_bash_sends_a_complete_request_as_an_argument():
    argv = http_over_bash("PUT", "/collections/c/snapshots/recover", {"location": "file:///x"})
    assert argv[:2] == ["bash", "-c"]
    request = argv[-1]
    body = json.dumps({"location": "file:///x"})
    assert request.startswith("PUT /collections/c/snapshots/recover HTTP/1.0\r\n")
    assert f"Content-Length: {len(body)}\r\n" in request
    assert request.endswith("\r\n\r\n" + body)


def test_http_over_bash_get_has_no_body():
    request = http_over_bash("GET", "/collections/c")[-1]
    assert request.endswith("\r\n\r\n")
    assert "Content-Length" not in request


def test_parse_http_response_returns_the_json_body():
    raw = b'HTTP/1.0 200 OK\r\ncontent-type: application/json\r\n\r\n{"result": true}'
    assert parse_http_response(raw) == {"result": True}


def test_parse_http_response_raises_on_an_error_status():
    raw = b'HTTP/1.0 400 Bad Request\r\n\r\n{"status": {"error": "bad snapshot"}}'
    with pytest.raises(SyncError, match="400.*bad snapshot"):
        parse_http_response(raw)


# --- write_tar ---------------------------------------------------------------


def test_write_tar_stores_files_under_their_base_names(tmp_path):
    src = tmp_path / "nested"
    src.mkdir()
    (src / "cards_2026-09-26.jsonl").write_bytes(b"card\n")
    buf = io.BytesIO()
    write_tar(buf, [src / "cards_2026-09-26.jsonl"])
    buf.seek(0)
    with tarfile.open(fileobj=buf) as tar:
        assert tar.getnames() == ["cards_2026-09-26.jsonl"]
        assert tar.extractfile("cards_2026-09-26.jsonl").read() == b"card\n"


# --- write_data_version -------------------------------------------------------


def test_write_data_version_writes_an_iso_timestamp(tmp_path):
    from datetime import UTC, datetime

    path = sync_data.write_data_version(tmp_path, datetime(2026, 9, 29, 15, 0, 7, tzinfo=UTC))
    assert path == tmp_path / "data_version"
    assert path.read_text(encoding="utf-8") == "2026-09-29T15:00:07+00:00\n"
