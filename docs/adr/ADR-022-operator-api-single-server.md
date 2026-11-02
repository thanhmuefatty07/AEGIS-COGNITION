# ADR-022: Single HTTP server authority for Operator Evidence API

Status: Accepted (recorded 2026-11-03)

## Context

`core/python/operator_api.py` previously defined two separate HTTP server
surfaces for the same read-only Operator Evidence API:
1. `serve_operator_evidence_api(root, host, port)` using the Python standard
   library `ThreadingHTTPServer` and `build_operator_http_handler(api)`, which
   is the server run in production by `core/python/operator_api_server.py`,
   `Dockerfile`, `deploy/docker-compose.yml`, and `deploy/kubernetes.yaml`.
2. `create_app(root)` using `Starlette` ASGI routes, which was never invoked by
   any deployment manifest or CLI entrypoint and existed solely for
   `tests/test_public_route_boundary.py`.

Maintaining two HTTP server wrappers meant that route boundary tests in
`tests/test_public_route_boundary.py` exercised an ASGI wrapper rather than the
actual HTTP server deployed in containers and Kubernetes pods.

## Decision

- `serve_operator_evidence_api` (`ThreadingHTTPServer` +
  `build_operator_http_handler`) in `core/python/operator_api.py` is the single
  canonical HTTP server implementation for the read-only Operator Evidence API.
- `create_app` (the duplicate Starlette ASGI server) is removed from
  `core/python/operator_api.py`.
- `tests/test_public_route_boundary.py` exercises `serve_operator_evidence_api`
  directly over ephemeral loopback sockets (`127.0.0.1:0`) via `httpx.get`,
  matching the production runtime surface.

## Options considered

1. Keep both `ThreadingHTTPServer` and `Starlette` in `operator_api.py`.
   Rejected: violates the single-owner rule and creates test-vs-production
   divergence on socket handling, headers, and status codes.
2. Switch production deployments (`Dockerfile`, `docker-compose.yml`,
   `kubernetes.yaml`) to an external ASGI server (`uvicorn` + `Starlette`).
   Rejected: adds unnecessary runtime dependencies to the minimal read-only
   operator container when stdlib `ThreadingHTTPServer` has zero third-party
   runtime attack surface.
3. Consolidate on `serve_operator_evidence_api` (`ThreadingHTTPServer`) and
   point `tests/test_public_route_boundary.py` at the real loopback HTTP server.
   Chosen: zero external runtime dependencies, zero duplication, and 100%
   test-to-production fidelity.

## Migration, security, performance, operations, rollback

- Migration removes `create_app` from `core/python/operator_api.py` and updates
  `tests/test_public_route_boundary.py` to use `serve_operator_evidence_api`.
  No external consumers or deployment manifests called `create_app`.
- Security is improved by eliminating an unused web framework surface from the
  operator module and ensuring all security hardening on
  `build_operator_http_handler` is directly exercised by every route test.
- Performance of the read-only operator summary endpoints (`/health`,
  `/snapshot`, `/artifacts`, `/production-closure`) remains bounded (`<= 50 ms`
  for `/health` with a 64 KiB healthcheck cap).
- Operations in `Dockerfile`, `deploy/docker-compose.yml`,
  `deploy/kubernetes.yaml`, and `core/python/operator_api_healthcheck.py`
  remain unchanged.
- Rollback is a single commit revert if an ASGI adapter is ever needed in the
  future.

## Evidence

- `scripts/architecture_fitness.py` enforces that `core/python/operator_api.py`
  contains `serve_operator_evidence_api` and does not define a second `Starlette`
  server (`create_app`).
- `tests/test_public_route_boundary.py` and
  `tests/test_operator_api_healthcheck.py` verify `/health`, `/snapshot`,
  `/artifacts`, and `/production-closure` over real loopback HTTP sockets.
