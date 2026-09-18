# YouTube MCP v0.2.0 — Practical Handbook

> A practical operator guide for `Dracoform/chatgpt-youtube-mcp` v0.2.0.
>
> This handbook is deliberately about **using** the project, not about the implementation history.

---

## 1. What this thing does

The project runs one private YouTube MCP core and lets different MCP clients reach it through one or more access methods.

The core exposes the YouTube tools. The access method decides **how the client reaches the core and how authentication is handled**.

You can enable these methods independently or together:

| Access method | Intended clients | Public internet required? | Authentication |
|---|---|---:|---|
| OpenAI Secure MCP Tunnel | ChatGPT / OpenAI | No direct port exposure | OpenAI tunnel |
| Public static-Bearer edge | LibreChat, DeepSeek Harness, generic MCP clients | Yes | `Authorization: Bearer ytsk_...` |
| Public OAuth edge | Claude / claude.ai, LibreChat | Yes | OAuth access token |
| Static + OAuth on one edge | Mixed clients | Yes | Both, deterministically separated |
| Local trusted access | Local MCP clients | No | None; trusted local only |

The OpenAI tunnel and the public edge can run at the same time against the same MCP core.

---

# 2. The short version

If you do not want to understand the architecture, use the generator.

There are three equivalent generators:

- Web generator
- Bash generator
- PowerShell generator

For the same choices they generate the same stack.

The easiest workflow is:

1. Decide which client(s) you want to use.
2. Run one generator.
3. Select the corresponding access method(s).
4. Put secrets in your local `.env`, never in committed files.
5. Start the generated Docker Compose stack.
6. Configure the MCP URL/authentication in the client.
7. Test `tools/list` or ask the client for a YouTube transcript.

For most users there is no reason to hand-write the Compose configuration.

---

# 3. Which mode should I choose?

## I only want ChatGPT

Choose:

**OpenAI Secure MCP Tunnel**

This is the original and simplest ChatGPT path. It remains independent of the new public edge.

You do **not** need:

- OAuth
- a public HTTPS port
- your own TLS certificate
- a static `ytsk_` token

The tunnel connects directly to the private MCP core.

---

## I want DeepSeek Harness

Choose:

**Public static-Bearer edge**

DeepSeek Harness currently uses Streamable HTTP with static request headers and does not drive an OAuth flow.

Use a token beginning with the reserved static namespace:

```text
ytsk_your-long-random-secret
```

and configure:

```text
Authorization: Bearer ytsk_your-long-random-secret
```

Do not expose the MCP core itself.

---

## I want LibreChat

You have two choices.

### Simpler

Use the **static-Bearer edge**.

### Per-user OAuth

Use the **OAuth edge** with a separate Authorization Server.

LibreChat supports both models, so choose based on how much identity infrastructure you actually need.

For a private/single-operator installation, static Bearer is much simpler.

---

## I want Claude / claude.ai Custom Connectors

Use the **OAuth edge**.

The project acts as the OAuth **Resource Server**. It does not contain its own user/account system.

You therefore also need a separate Authorization Server, for example the reference setup used during development with Keycloak.

The implemented OAuth flow and metadata were validated against the documented Claude connector requirements and with a real OAuth/MCP protocol path. A live hosted claude.ai connector test remains account/public-endpoint dependent.

---

## I want local LM Studio / another local MCP client

Choose:

**Local trusted access**

Keep it loopback-only.

Do not expose unauthenticated local mode to the public internet.

---

# 4. Basic installation

You need a machine with Docker Engine and Docker Compose.

Check:

```bash
docker --version
docker compose version
```

Pulling the released container directly is possible with:

```bash
docker pull ghcr.io/dracoform/chatgpt-youtube-mcp:0.2.0
```

Available release tags include:

```text
latest
0.2
0.2.0
```

For reproducible deployments, prefer the full version:

```text
ghcr.io/dracoform/chatgpt-youtube-mcp:0.2.0
```

---

# 5. Secrets: where they belong

The repository contains `.env.example`.

That file is an **example and is committed**.

Never put real secrets into `.env.example`.

Create your real local configuration from it:

```bash
cp .env.example .env
```

Then edit:

```text
.env
```

The real `.env` is intended to stay local/gitignored.

Typical secrets include:

- YouTube API key
- OpenAI tunnel/runtime credentials
- static MCP Bearer tokens
- OAuth-related confidential configuration, depending on your Authorization Server

Never commit:

```text
.env
edge-certs/
private keys
real Bearer tokens
Authorization headers
```

---

# 6. Using the generator

## Web generator

Use the hosted generator linked from the repository README.

The generator runs client-side and is designed not to send your entered values elsewhere.

Choose the access methods you need. The form only exposes the settings relevant to those choices.

Typical choices are:

```text
OpenAI tunnel
Static public edge
OAuth public edge
Local access
```

Static and OAuth can both be enabled on the same edge.

Copy the generated Compose YAML into your deployment environment, for example Portainer.

---

## Bash generator

Run the repository's Bash generator:

```bash
bash generators/generate_docker-compose_for_ChatGPT_MCP.sh
```

Despite the historical filename, the generator is now multi-client/capability-oriented.

---

## PowerShell generator

Run:

```powershell
.\generators\generate_docker-compose_for_ChatGPT_MCP.ps1
```

Bash, PowerShell and Web output are tested for equivalence.

---

# 7. Static-Bearer public edge

This is the practical remote mode for DeepSeek Harness, LibreChat, and generic clients that can attach HTTP headers.

## Create a token

Use a long random secret beginning with:

```text
ytsk_
```

Example shape only:

```text
ytsk_<long-random-value>
```

Do not use the example literally.

The prefix is important because when static auth and OAuth coexist, the edge uses it as a deterministic authentication-domain selector:

```text
starts with ytsk_  -> static validation only
anything else      -> OAuth validation only
```

There is no fallback between the two.

---

## Configure static auth

Relevant settings include:

```text
EDGE_STATIC_AUTH_ENABLED=true
EDGE_STATIC_TOKENS=ytsk_<your-secret>
EDGE_STATIC_TOKEN_PREFIX=ytsk_
```

Multiple static tokens can be configured for rotation.

If static authentication is enabled but no token is configured, the edge refuses to start.

That is intentional.

---

## Client header

Configure the client to send:

```http
Authorization: Bearer ytsk_<your-secret>
```

The MCP endpoint is:

```text
https://YOUR-HOST/mcp
```

The exact hostname/port depends on your TLS/public deployment.

---

# 8. Public HTTPS

There are two supported TLS layouts.

## Option A — the MCP edge terminates TLS

Provide both certificate and private key.

Typical settings:

```text
EDGE_TLS_CERT_FILE=/certs/tls.crt
EDGE_TLS_KEY_FILE=/certs/tls.key
EDGE_TLS_DIR=./edge-certs
EDGE_PUBLIC_HTTPS_PORT=8443
```

The cert and key are both required. Supplying only one is a configuration error.

### Host bind address

The safe default is:

```text
EDGE_PUBLIC_BIND_ADDRESS=127.0.0.1
```

That means the Docker-published port is reachable only from the host.

For a genuinely directly reachable public edge, deliberately change it to:

```text
EDGE_PUBLIC_BIND_ADDRESS=0.0.0.0
```

Do this only when:

- TLS is correctly configured,
- authentication is enabled,
- the firewall is intentional,
- you actually want the edge reachable from outside.

The project intentionally does **not** silently bind the public edge to every interface.

---

## Option B — existing reverse proxy / ingress

If you already use:

- Caddy
- nginx
- Traefik
- another TLS-terminating ingress

let that component handle public HTTPS and forward to the MCP edge over trusted/private HTTP.

In this layout you do not need the edge itself to perform TLS termination.

The MCP core must still stay private.

---

# 9. Docker Compose profiles

The repository contains profiles for the edge deployment paths.

Local static edge:

```bash
docker compose --profile edge up -d
```

Public/TLS edge:

```bash
docker compose --profile edge-public up -d
```

Inspect:

```bash
docker compose ps
docker compose logs
```

Stop again with:

```bash
docker compose --profile edge down
```

or:

```bash
docker compose --profile edge-public down
```

depending on what you started.

The generated stacks are preferable when combining multiple access methods.

---

# 10. OpenAI / ChatGPT

For ChatGPT, use the OpenAI Secure MCP Tunnel path.

Conceptually:

```text
ChatGPT
   |
OpenAI Secure MCP Tunnel
   |
private YouTube MCP core
```

The public static/OAuth edge is not required for this path.

That means existing tunnel-only deployments do not need to migrate to OAuth or public HTTPS merely because v0.2.0 added those features.

Use the generator, select the OpenAI tunnel capability, and provide the tunnel/runtime values requested by the generator.

The tunnel remains directly wired to the core.

---

# 11. DeepSeek Harness

Use:

```text
transport: streamable-http
```

and send a static Authorization header.

Conceptually:

```yaml
transport: streamable-http
headers:
  Authorization: Bearer ytsk_<secret>
```

The exact surrounding DeepSeek Harness configuration structure depends on the Harness version/config file.

Important behavior:

- static Bearer works
- OAuth is not required
- use `/mcp`
- use HTTPS for a remote deployment
- do not omit the Authorization header and expect an OAuth flow

The project's protocol path has been validated with the official MCP SDK; a live end-user DeepSeek Harness instance remains environment-dependent.

---

# 12. LibreChat

## Static configuration

Use Streamable HTTP and send:

```text
Authorization: Bearer ytsk_<secret>
```

This is the easiest LibreChat deployment.

## OAuth configuration

LibreChat can also use the OAuth edge.

That requires:

1. public HTTPS MCP edge,
2. OAuth enabled on the edge,
3. a separate Authorization Server,
4. correct issuer/resource/audience/scope configuration,
5. LibreChat configured for that OAuth MCP endpoint.

For a single trusted operator, static auth is usually operationally simpler.

---

# 13. Claude / OAuth

The MCP edge is an OAuth **Resource Server**, not an Authorization Server.

The deployment therefore looks like:

```text
Claude
   |
   | OAuth access token
   v
Public MCP Edge
   |
   | validated request
   v
Private YouTube MCP Core

        +
Separate Authorization Server
```

A separate Authorization Server handles:

- login/users,
- Authorization Code flow,
- PKCE,
- token issuance,
- refresh,
- client registration where required,
- signing keys.

Keycloak was used as the reference Authorization Server during implementation, but it is not bundled or required.

---

## Main OAuth edge settings

Typical configuration includes:

```text
EDGE_OAUTH_ENABLED=true
EDGE_OAUTH_ISSUER=https://auth.example.org/realms/mcp
EDGE_OAUTH_RESOURCE_IDENTIFIER=https://mcp.example.org/mcp
EDGE_OAUTH_AUDIENCE=<expected-audience>
EDGE_OAUTH_REQUIRED_SCOPE=<scope>
```

Optional:

```text
EDGE_OAUTH_JWKS_URL=
EDGE_OAUTH_AUTHORIZATION_SERVERS=
EDGE_OAUTH_SCOPES_SUPPORTED=
EDGE_OAUTH_CLOCK_SKEW_SECONDS=5
```

The edge can discover JWKS from the issuer unless an explicit JWKS URL is configured.

The edge validates, as configured:

- JWT signature,
- issuer,
- expiry / not-before,
- audience,
- required scope,
- permitted asymmetric algorithms.

Invalid OAuth tokens do not fall back to static authentication.

---

# 14. Static + OAuth together

This is one of the useful v0.2.0 features.

You do **not** need two separate public MCP edge services.

One edge can accept:

```text
DeepSeek -> ytsk_ static token
LibreChat -> ytsk_ static token OR OAuth token
Claude -> OAuth token
```

The dispatcher is deterministic:

```text
ytsk_* -> static validator
other Bearer token -> OAuth validator
```

Therefore:

- an invalid static token never gets tried as OAuth,
- an invalid OAuth token never gets tried as static.

---

# 15. Local trusted mode

Use local/no-auth only when the endpoint is actually trusted.

Safe idea:

```text
127.0.0.1
```

Potentially acceptable with deliberate network controls:

```text
trusted private LAN
```

Bad idea:

```text
0.0.0.0 + no auth + public internet
```

Public no-auth is not a supported production deployment.

---

# 16. Testing whether it works

## Health

Check the relevant service:

```bash
docker compose ps
```

and inspect logs:

```bash
docker compose logs --tail=100
```

## MCP endpoint

A correct MCP client should connect to:

```text
/mcp
```

A plain browser or simplistic HTTP request is not necessarily a valid MCP protocol test.

A `406` may simply mean the request did not provide the media/protocol headers expected by the MCP server.

## Authentication behavior

For a protected public edge:

```text
missing token  -> 401
bad token      -> 401
valid token    -> MCP request reaches core
```

For OAuth with insufficient scope:

```text
403
```

## Functional test

Ask the client to:

- list available MCP tools, or
- retrieve a transcript for a known public YouTube video.

If `tools/list` works but a particular transcript fails, the MCP connection itself is probably fine; troubleshoot YouTube/transcript retrieval separately.

---

# 17. Common problems

## Edge refuses to start

Check:

- static auth enabled but `EDGE_STATIC_TOKENS` empty,
- OAuth enabled but issuer/resource missing,
- only TLS certificate or only TLS key configured,
- unreadable/missing certificate files.

The edge intentionally fails closed.

---

## It works on the server but not remotely

Check:

```text
EDGE_PUBLIC_BIND_ADDRESS
```

Safe default:

```text
127.0.0.1
```

For direct remote access you may need:

```text
0.0.0.0
```

plus firewall/TLS/authentication.

Also check cloud/router/firewall port forwarding.

---

## Claude cannot connect

Check:

- endpoint is public HTTPS,
- `/mcp` is correct,
- OAuth protected-resource metadata is reachable,
- issuer metadata/JWKS are reachable,
- token audience/resource matches,
- required scope matches,
- your Authorization Server/client-registration setup matches Claude's requirements.

Do not diagnose a Claude OAuth problem by weakening edge authentication.

---

## DeepSeek gets 401

DeepSeek static clients need the header on the request:

```text
Authorization: Bearer ytsk_<secret>
```

Check the token prefix and configured token list.

Do not expect DeepSeek Harness to complete an OAuth flow.

---

## OAuth token gets 401

Check:

- issuer,
- audience,
- expiry,
- `nbf`,
- JWKS,
- signing algorithm,
- `kid`,
- resource identifier.

If the Authorization Server/JWKS is unavailable when validation cannot be completed, the edge fails closed rather than accepting the request.

---

## OAuth token gets 403

Usually:

```text
required scope missing
```

Compare the token scope with:

```text
EDGE_OAUTH_REQUIRED_SCOPE
```

---

## Transcript is truncated

The server has transcript-size limits and pagination support.

Do not confuse a transcript-size limit with an MCP transport failure.

---

# 18. Updating

For released deployments, use explicit release tags where possible.

Example:

```text
ghcr.io/dracoform/chatgpt-youtube-mcp:0.2.0
```

Then update deliberately rather than silently moving with `latest`.

Typical flow:

```bash
docker compose pull
docker compose up -d
```

For locally built stacks, rebuild as appropriate.

Always read the release notes before changing between versions.

---

# 19. Security rules worth remembering

1. Never expose the MCP core directly to the internet.
2. Public access goes through the edge.
3. Public edge must have static auth and/or OAuth.
4. Keep real secrets in `.env`, not `.env.example`.
5. Keep TLS private keys out of Git.
6. Use long random `ytsk_` tokens.
7. Prefer explicit release image tags for production.
8. Do not disable JWT validation to fix OAuth configuration.
9. Do not turn local no-auth into public no-auth.
10. Keep the default loopback bind unless you deliberately want public exposure.

---

# 20. What should I use? — cheat sheet

| Your situation | Recommended mode |
|---|---|
| “I just want ChatGPT to use YouTube.” | OpenAI Secure MCP Tunnel |
| “I use DeepSeek Harness.” | Static Bearer edge |
| “I use LibreChat at home.” | Static Bearer edge |
| “I need LibreChat per-user login.” | OAuth edge |
| “I want Claude Custom Connector.” | OAuth edge |
| “I run everything locally.” | Local trusted access |
| “I use Claude + DeepSeek + ChatGPT.” | Tunnel + one public edge with static + OAuth |
| “I already have nginx/Caddy/Traefik.” | External TLS ingress + edge |
| “I do not have an ingress but need public HTTPS.” | Edge TLS + deliberate public bind |

---

# 21. Recommended first deployment

If this is your first time using v0.2.0, do **not** start with OAuth unless Claude specifically requires it.

A sensible learning path is:

### Step 1

Run tunnel-only for ChatGPT or static-Bearer for LibreChat/DeepSeek.

### Step 2

Confirm:

```text
health -> OK
auth -> OK
tools/list -> OK
real transcript -> OK
```

### Step 3

Only then add OAuth if you need Claude/per-user authentication.

### Step 4

Once each path works separately, enable several access methods together.

This keeps authentication, networking and YouTube extraction problems separate while you learn the stack.

---

# 22. Release status for v0.2.0

v0.2.0 was release-verified with:

- full Python test suite,
- Web-generator test suite,
- Bash/PowerShell/Web generator equivalence,
- Compose validation,
- real Docker edge/core E2E,
- static Bearer E2E,
- OAuth E2E against real Keycloak,
- real MCP SDK session,
- real YouTube transcript tool calls through static and OAuth paths.

Some final vendor-client E2E remains dependent on external accounts/instances, notably a live hosted Claude connector and live LibreChat/DeepSeek installations.

---

## Final rule

If you are confused, start with this question:

> **Which client needs to reach the MCP?**

Then choose its access method. Everything else follows from that.

