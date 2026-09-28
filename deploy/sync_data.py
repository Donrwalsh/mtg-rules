"""Copy this machine's data into the production stack (README "Production
deployment" -> "Seeding the data").

Takes a snapshot of the local Qdrant collection and the latest parsed
cards_*.jsonl / rules_*.jsonl, and loads them into the Coolify-deployed
stack over SSH:

    python deploy/sync_data.py --host root@your-server

Nothing is staged on the server's disk. The snapshot streams into the qdrant
container (`docker cp -`) and is recovered from there, the JSONL streams
into the parsed-data volume through a throwaway alpine container, and then
the backend is restarted (it loads the parsed files only at startup).

Coolify prefixes container, volume and network names with a generated
UUID, so the target is discovered instead of configured: the backend is
the container with a *volume* mounted at /app/data/parsed, and qdrant is
the container of the same compose project. --local runs the same
server-side commands against the local Docker instead (for testing
against a local copy of docker-compose.prod.yml).

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

PARSED_MOUNT = "/app/data/parsed"
PROJECT_LABEL = "com.docker.compose.project"
SERVICE_LABEL = "com.docker.compose.service"
SNAPSHOT_NAME = "mtg-sync.snapshot"
QDRANT_SNAPSHOT_DIR = "/qdrant/snapshots"
DEFAULT_PARSED_DIR = Path(__file__).resolve().parent.parent / "mtg-worker/mtg-ingestion/data/parsed"


class SyncError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    project: str
    backend: str
    qdrant: str
    parsed_volume: str
    # Every container in the stack, started at the end.
    containers: tuple[str, ...]


def latest_parsed_files(parsed_dir: Path) -> list[Path]:
    """The newest cards_*.jsonl and rules_*.jsonl -- the two files the
    backend loads, picked the same way it picks them (last by name)."""
    files = []
    for kind in ("cards", "rules"):
        matches = sorted(parsed_dir.glob(f"{kind}_*.jsonl"))
        if not matches:
            raise SyncError(f"no {kind}_*.jsonl in {parsed_dir}")
        files.append(matches[-1])
    return files


def _parsed_volume(container: dict) -> str | None:
    for mount in container.get("Mounts") or []:
        if mount.get("Destination") == PARSED_MOUNT and mount.get("Type") == "volume":
            return mount["Name"]
    return None


def find_target(containers: list[dict], project: str | None = None) -> Target:
    """Pick the prod stack out of `docker inspect` output for every container
    on the host."""

    def labels(c: dict) -> dict:
        return (c.get("Config") or {}).get("Labels") or {}

    backends = [c for c in containers if _parsed_volume(c)]
    if project is not None:
        backends = [c for c in backends if labels(c).get(PROJECT_LABEL) == project]
    if not backends:
        where = f" in project {project!r}" if project else ""
        raise SyncError(
            f"no backend container{where} with a volume at {PARSED_MOUNT} -- deploy the stack first"
        )
    projects = sorted({labels(c).get(PROJECT_LABEL, "?") for c in backends})
    if len(projects) > 1:
        raise SyncError(f"several stacks match ({', '.join(projects)}); choose one with --project")
    backend = backends[0]
    stack = labels(backend).get(PROJECT_LABEL)
    members = [c for c in containers if labels(c).get(PROJECT_LABEL) == stack]
    qdrants = [c for c in members if labels(c).get(SERVICE_LABEL) == "qdrant"]
    if not qdrants:
        raise SyncError(f"no qdrant container in project {stack!r}")
    return Target(
        project=stack,
        backend=backend["Id"],
        qdrant=qdrants[0]["Id"],
        parsed_volume=_parsed_volume(backend),
        containers=tuple(c["Id"] for c in members),
    )


def remote_argv(host: str | None, argv: list[str]) -> list[str]:
    """argv to run `argv` on the server: through ssh (quoted for the remote
    shell), or as-is when host is None (--local)."""
    if host is None:
        return argv
    return ["ssh", host, shlex.join(argv)]


def http_over_bash(method: str, path: str, body: Any = None) -> list[str]:
    """argv that makes one HTTP request to Qdrant from inside its own
    container. The image has bash but no curl; the full request is passed
    as an argument, so nothing needs escaping."""
    lines = [f"{method} {path} HTTP/1.0", "Host: localhost"]
    payload = ""
    if body is not None:
        payload = json.dumps(body)
        lines += ["Content-Type: application/json", f"Content-Length: {len(payload.encode())}"]
    request = "\r\n".join(lines) + "\r\n\r\n" + payload
    script = 'exec 3<>/dev/tcp/127.0.0.1/6333 && printf "%s" "$1" >&3 && cat <&3'
    return ["bash", "-c", script, "http", request]


def parse_http_response(raw: bytes) -> Any:
    head, _, body = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise SyncError(f"unexpected response from qdrant: {raw[:200]!r}")
    status = int(parts[1])
    text = body.decode(errors="replace")
    if not 200 <= status < 300:
        raise SyncError(f"qdrant returned {status}: {text[:500]}")
    return json.loads(text)


def write_tar(stream: IO[bytes], files: list[Path]) -> None:
    with tarfile.open(fileobj=stream, mode="w|") as tar:
        for path in files:
            tar.add(path, arcname=path.name)


class Server:
    def __init__(self, host: str | None):
        self.host = host

    def run(self, argv: list[str]) -> bytes:
        result = subprocess.run(remote_argv(self.host, argv), capture_output=True, check=False)
        if result.returncode != 0:
            raise SyncError(
                f"`{shlex.join(argv)[:200]}` failed ({result.returncode}): "
                f"{result.stderr.decode(errors='replace').strip()}"
            )
        return result.stdout

    def run_with_tar(self, argv: list[str], files: list[Path]) -> None:
        proc = subprocess.Popen(
            remote_argv(self.host, argv), stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            write_tar(proc.stdin, files)
        finally:
            proc.stdin.close()
        stderr = proc.stderr.read()
        if proc.wait() != 0:
            raise SyncError(
                f"`{shlex.join(argv)}` failed ({proc.returncode}): {stderr.decode(errors='replace')}"
            )

    def qdrant(self, container: str, method: str, path: str, body: Any = None) -> Any:
        return parse_http_response(
            self.run(["docker", "exec", container, *http_over_bash(method, path, body)])
        )


def _local_qdrant(url: str, method: str, path: str, body: Any = None) -> Any:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def _download_snapshot(qdrant_url: str, collection: str, dest: Path) -> None:
    info = _local_qdrant(qdrant_url, "POST", f"/collections/{collection}/snapshots")["result"]
    try:
        url = f"{qdrant_url}/collections/{collection}/snapshots/{info['name']}"
        with urllib.request.urlopen(url) as resp, dest.open("wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
    finally:
        _local_qdrant(qdrant_url, "DELETE", f"/collections/{collection}/snapshots/{info['name']}")


def _count(result: Any) -> int:
    return result["result"]["count"]


def _wait_healthy(server: Server, container: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    status = "?"
    while time.monotonic() < deadline:
        status = (
            server.run(["docker", "inspect", "-f", "{{.State.Health.Status}}", container])
            .decode()
            .strip()
        )
        if status == "healthy":
            return
        time.sleep(5)
    raise SyncError(f"backend not healthy after {timeout:.0f}s (last status: {status})")


def sync(
    server: Server,
    *,
    parsed_dir: Path,
    qdrant_url: str,
    collection: str,
    project: str | None,
    health_timeout: float,
) -> None:
    files = latest_parsed_files(parsed_dir)

    print("Finding the stack on the server...")
    ids = server.run(["docker", "ps", "-aq"]).decode().split()
    containers = json.loads(server.run(["docker", "inspect", *ids])) if ids else []
    target = find_target(containers, project)
    print(f"  project {target.project}, parsed volume {target.parsed_volume}")

    local_count = _count(
        _local_qdrant(
            qdrant_url, "POST", f"/collections/{collection}/points/count", {"exact": True}
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / SNAPSHOT_NAME
        print(f"Snapshotting local collection {collection!r} ({local_count} points)...")
        _download_snapshot(qdrant_url, collection, snapshot)
        size_mb = snapshot.stat().st_size / 1e6
        print(f"Uploading snapshot ({size_mb:.0f} MB)...")
        server.run_with_tar(
            ["docker", "cp", "-", f"{target.qdrant}:{QDRANT_SNAPSHOT_DIR}"], [snapshot]
        )

    print("Recovering the collection on the server...")
    location = f"file://{QDRANT_SNAPSHOT_DIR}/{SNAPSHOT_NAME}"
    try:
        server.qdrant(
            target.qdrant,
            "PUT",
            f"/collections/{collection}/snapshots/recover?wait=true",
            {"location": location, "priority": "snapshot"},
        )
    finally:
        server.run(["docker", "exec", target.qdrant, "rm", "-f", location[len("file://") :]])
    remote_count = _count(
        server.qdrant(
            target.qdrant, "POST", f"/collections/{collection}/points/count", {"exact": True}
        )
    )
    if remote_count != local_count:
        raise SyncError(f"server has {remote_count} points after recovery, expected {local_count}")
    print(f"  {remote_count} points")

    print(f"Copying {', '.join(p.name for p in files)}...")
    server.run_with_tar(
        [
            "docker", "run", "-i", "--rm", "-v", f"{target.parsed_volume}:/d", "alpine",
            "sh", "-c", "rm -f /d/cards_*.jsonl /d/rules_*.jsonl && tar x -C /d",
        ],
        files,
    )  # fmt: skip

    print("Restarting the backend...")
    server.run(["docker", "restart", target.backend])
    _wait_healthy(server, target.backend, health_timeout)
    # On a first deploy the frontend gave up waiting for a healthy backend
    # and never started; `docker start` is a no-op for running containers.
    server.run(["docker", "start", *target.containers])
    print("Done: backend healthy, stack started.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--host", help="SSH destination of the server, e.g. root@1.2.3.4")
    where.add_argument("--local", action="store_true", help="target the local Docker (testing)")
    parser.add_argument("--project", help="compose project, when several stacks match")
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="local Qdrant")
    parser.add_argument("--collection", default="mtg_rules")
    parser.add_argument(
        "--health-timeout", type=float, default=300, help="seconds to wait for the backend"
    )
    args = parser.parse_args(argv)
    # Progress lines appear as they happen, even when piped.
    sys.stdout.reconfigure(line_buffering=True)
    try:
        sync(
            Server(None if args.local else args.host),
            parsed_dir=args.parsed_dir,
            qdrant_url=args.qdrant_url.rstrip("/"),
            collection=args.collection,
            project=args.project,
            health_timeout=args.health_timeout,
        )
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
