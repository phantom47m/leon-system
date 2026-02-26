---
name: Docker
slug: docker
version: 1.0.2
description: Build, secure, and deploy Docker containers with image optimization, networking, and production-ready patterns.
---

## When to Use

User needs Docker expertise — from building images to production deployments. Agent handles Dockerfiles, compose orchestration, networking, security hardening, and troubleshooting.

## Quick Reference

| Topic | File |
|-------|------|
| Dockerfile best practices | `images.md` |
| Docker Compose patterns | `compose.md` |
| Networking and volumes | `infrastructure.md` |
| Security hardening | `security.md` |

## Image Building Traps

- `apt-get update` and `apt-get install` in separate RUN layers = stale packages weeks later — always combine them
- `python:latest` today is different than `python:latest` tomorrow — pin versions like `python:3.11.5-slim`
- Multi-stage builds: forgotten `--from=builder` copies from wrong stage silently
- COPY before RUN invalidates cache on every file change — copy requirements first, install, then copy code

## Runtime Crashes

- Default log driver has no size limit — one chatty container fills disk and crashes host
- OOM killer strikes without warning — set memory limits with `-m 512m` on every container
- Container runs as root by default — add `USER nonroot` or security scans fail and platforms reject
- `localhost` inside container is container's localhost, not host — bind to `0.0.0.0`

## Networking Traps

- Container DNS only works on custom networks — default bridge can't resolve container names
- Published ports bind to `0.0.0.0` by default — use `127.0.0.1:5432:5432` for local-only
- Zombie connections from killed containers — set proper health checks and restart policies
- Port already in use: previous container still stopping — wait or force remove

## Compose Traps

- `depends_on` waits for container start, not service ready — use `condition: service_healthy` with healthcheck
- `.env` file in wrong directory silently ignored — must be next to docker-compose.yml
- Volume mounts overwrite container files — empty host dir = empty container dir
- YAML anchors don't work across files — extends deprecated, use multiple compose files

## Volumes and Data

- Anonymous volumes from Dockerfile VOLUME instruction accumulate silently — use named volumes
- Bind mounts have host permission issues — container user must match host user or use `:z` suffix
- `docker system prune` doesn't remove named volumes — add `-volumes` flag explicitly
- Stopped container data persists until container removed — `docker rm` deletes data

## Resource Leaks

- Dangling images grow unbounded — `docker image prune` regularly
- Build cache grows forever — `docker builder prune` reclaims space
- Stopped containers consume disk — `docker container prune` or `--rm` on run
- Networks pile up from compose projects — `docker network prune`

## Secrets and Security

- ENV and COPY bake secrets into layer history permanently — use secrets mount or runtime env
- `--privileged` disables all security — almost never needed, find specific capability instead
- Images from unknown registries may be malicious — verify sources
- Build args visible in image history — don't use for secrets

## Debugging

- Exit code 137 = OOM killed, 139 = segfault — check `docker inspect --format='{{.State.ExitCode}}'`
- Container won't start: check logs even for failed containers — `docker logs <container>`
- No shell in distroless images — `docker cp` files out or use debug sidecar
- Inspect filesystem of dead container — `docker cp deadcontainer:/path ./local`
