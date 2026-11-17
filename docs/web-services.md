# Local Web Services

`web_search` and `web_fetch` depend on two loopback HTTP services. Nothing in
the app starts them; provision them once with Compose, or run equivalents
yourself.

| Tool | Service | Default URL | Override |
| --- | --- | --- | --- |
| `web_search` | SearXNG | `http://127.0.0.1:8080` | `OPENBAD_SEARXNG_URL` |
| `web_fetch` | Jina Reader | `http://127.0.0.1:3000` | `OPENBAD_JINA_READER_URL` |

`web_search` **requires** SearXNG. There is no built-in fallback search
backend, so an unreachable or misconfigured instance fails with an actionable
error instead of silently switching providers. `web_fetch` degrades: it uses
the reader when it can and falls back to direct HTTP when the reader is
disabled or unreachable.

## Start

From the repository root:

```bash
docker compose up -d
docker compose ps
```

Docker Compose v2 (`docker compose version`) is required; the `docker.io`
package does not ship it, so on Ubuntu install `docker-compose-v2` first.

The compose file binds both published ports to `127.0.0.1` and restarts the
containers unless you stop them. It mounts
[`docker/searxng/settings.yml`](../docker/searxng/settings.yml), which enables
the `json` output format `web_search` needs and turns off SearXNG's limiter.

Verify:

```bash
curl -s 'http://127.0.0.1:8080/search?q=openbad&format=json' | head -c 200
curl -s 'http://127.0.0.1:3000/https://example.com' | head -20
```

Stop with `docker compose down`; add `-v` to drop the SearXNG cache volume as
well.

## Migrating from hand-created containers

If the services already run as plain `docker run` containers named `searxng`
and `jina-reader`, Compose cannot claim their ports. Stop and remove them once,
then start the stack:

```bash
docker stop searxng jina-reader
docker rm searxng jina-reader
docker compose up -d
```

The replacement containers keep the same loopback ports, so no environment
variable change is needed.

## Configuration

- `OPENBAD_SEARXNG_URL` and `OPENBAD_JINA_READER_URL` change the base URL the
  tools use. A trailing slash is optional.
- `off`, `none`, `disabled`, or `direct` in either variable switches that
  service off. `web_search` then fails with a "switched off" error;
  `web_fetch` skips the reader and fetches directly.
- Custom ports: change the published port in
  [`docker-compose.yml`](../docker-compose.yml) and set the matching
  environment variable, for example
  `OPENBAD_SEARXNG_URL=http://127.0.0.1:8888`.
- Exposing the instance beyond loopback: give SearXNG a real
  `server.secret_key` in [`docker/searxng/settings.yml`](../docker/searxng/settings.yml)
  (or set `SEARXNG_SECRET`) first. The committed file ships a loopback-only
  placeholder because SearXNG refuses to start on its own development default.
- Docker Hub rate limits: set `SEARXNG_VERSION` to a tag, or point the compose
  service at the GHCR mirror `ghcr.io/searxng/searxng`.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `SearXNG is not reachable at http://127.0.0.1:8080 (TCP ...: Connection refused)` | Container is not running | `docker compose up -d searxng` |
| `timed out after 15 s; the endpoint did not answer before the request deadline` | Something holds the port without answering, or packets are dropped | `docker compose ps`, then `docker compose logs searxng` |
| `returned HTTP 403` or `returned a non-JSON body` | `json` missing from `search.formats` | use the committed settings file |
| `Jina Reader is not configured` with `reader: "jina"` | Reader switched off | unset the variable or use `reader: "auto"` |
| `web_fetch` returns less structure than expected | Reader unreachable, direct fallback used | check `metadata.via == "direct"` and the services panel |

`web_search` probes a loopback endpoint with a short TCP connect before sending
the HTTP request, so a stopped local service fails in milliseconds rather than
after the request timeout. Remote endpoints skip the probe because an egress
proxy can make a direct TCP connect fail while HTTP still works.

The desktop's services panel reports the same two endpoints through the same
probe.
