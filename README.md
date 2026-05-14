# Bazarr AI Proxy Provider

Custom Bazarr provider shim plus a worker container for smarter subtitle search and optional local OpenAI-compatible scoring.

The Bazarr provider stays inside Bazarr's normal provider pipeline. The worker does not upload subtitles or edit Bazarr state directly. Bazarr still handles download selection, saving, history, wanted cleanup, and notifications.

## Components

- `provider/aiproxy.py`: provider shim copied into Bazarr's `subliminal_patch.providers` package. It runs Bazarr's built-in providers first, then falls back to the worker.
- `worker/app`: FastAPI worker that reads Bazarr config and queries Subsource.
- `bazarr-image/Dockerfile`: derives from `lscr.io/linuxserver/bazarr:latest` and installs the shim.
- `worker/Dockerfile`: worker image.
- `docker-compose.example.yml`: starter compose file.

## Current Scope

- Built-in backend: every Bazarr provider currently enabled, excluding `aiproxy` itself.
- Worker fallback backend: Subsource first.
- Language: whatever Bazarr requests, if mapped in `worker/app/language_map.py`.
- AI: optional local OpenAI-compatible `/v1/chat/completions` endpoint.
- Import: handled by Bazarr provider pipeline, not by the worker.

## Search Order

`aiproxy` is `builtin-first` by default:

1. Inside Bazarr, `aiproxy` creates a Bazarr provider pool using existing provider credentials from Bazarr settings.
2. It searches enabled providers except `aiproxy` to avoid recursion.
3. It scores candidates using Bazarr's own `ComputeScore`, minimum score, blacklist, throttling, and provider configs.
4. If any candidate passes Bazarr's built-in threshold, it is returned through `aiproxy` and downloaded through the original provider.
5. If no built-in candidate passes, `aiproxy` keeps relaxed candidates from all configured providers when they match the requested language and an ID/hash match.
6. Those relaxed candidates are sent to the worker `/v1/score` endpoint for AI scoring.
7. If AI accepts a relaxed candidate, `aiproxy` returns it and downloads it through the original provider.
8. If AI accepts nothing, `aiproxy` can still call the worker `/v1/search` endpoint for enhanced Subsource title fallback.

This keeps Bazarr's normal rules first and uses AI only as a rescue path across all configured providers.

## Build

Clone the repo, create `.env`, then build and start:

```bash
cp .env.example .env
```

Edit `.env` and set:

- `/path/to/bazarr/config`
- `/path/to/media`
- `OPENAI_BASE_URL`
- `OPENAI_MODEL`

Start the stack:

```bash
docker compose up -d --build
```

`docker-compose.example.yml` is kept as a readable reference, but `compose.yaml` is the setup file used by default.

## Enable Provider

After building the custom Bazarr image, enable `aiproxy` in Bazarr.

If the UI shows it as a provider, enable it there.

If not, stop Bazarr and add `aiproxy` to `/config/config/config.yaml`:

```yaml
general:
  enabled_providers:
    - subsource
    - aiproxy
```

Use `subsource + aiproxy` for augment mode while testing. This means Bazarr will search `subsource` natively and `aiproxy` will also search it internally, so expect duplicate provider calls during tests.

For production replace mode, set Bazarr's enabled providers to only `aiproxy`, then provide real backend providers through an environment variable:

```yaml
environment:
  AIPROXY_BUILTIN_PROVIDERS: subsource,opensubtitlescom,jimaku
```

Credentials still come from Bazarr config. This avoids duplicate searches while keeping all searches inside the custom provider result path.

## Required Bazarr Config

The worker reads Subsource credentials from Bazarr's config:

```yaml
subsource:
  apikey: your-subsource-api-key
```

You can override it with `SUBSOURCE_API_KEY` on the worker if needed.

## Local AI Endpoint

Configure any OpenAI-compatible local endpoint:

```yaml
environment:
  OPENAI_BASE_URL: http://local-openai-compatible:8000/v1
  OPENAI_API_KEY: dummy
  OPENAI_MODEL: local-model
  AI_ENABLED: "true"
```

In the current `builtin-first` flow, built-in provider candidates that pass Bazarr's own score are returned without AI. AI is only used for relaxed fallback candidates that have target language plus an ID/hash match.

## Health Check

```bash
curl http://localhost:8787/healthz
```

From inside the Docker network, Bazarr calls:

```text
http://bazarr-ai-provider:8787/v1/search
```

## Test Case

For `Witch Hat Atelier`, run a manual search in Bazarr after enabling `aiproxy`.

Expected behavior:

- Bazarr invokes `aiproxy` as a normal provider.
- `aiproxy` first tries Bazarr's built-in providers and built-in score rules.
- If built-in providers find a good result, `aiproxy` returns that result immediately.
- If built-in scoring fails, `aiproxy` asks AI to score relaxed candidates from all configured providers that have matching ID/hash and target language.
- If AI accepts nothing, the worker tries enhanced Subsource by IMDb or title fallback.
- Worker accepts candidates that match requested language, season, and episode.
- Bazarr downloads and saves the selected subtitle through its normal flow.

## Notes

- `lscr.io/linuxserver/bazarr:latest` can change internal paths. The Dockerfile finds the provider directory at build time, but pinning a Bazarr tag/digest is recommended after this works.
- The worker mounts Bazarr config read-only and masks no logs beyond avoiding API key output. Do not run it with debug logs in an untrusted environment.
- Candidate cache is in memory. Restarting the worker clears search candidates; just run search again.
