# FLY

**FLY is an open-source selective proxy / policy-based routing frontend for Mihomo.**

It is designed for users who already have their own Clash/Mihomo subscription or self-managed nodes and only want certain region-restricted apps, games or websites to use a Japanese proxy. Everything that does not match an enabled profile stays on the user's normal local network through `MATCH,DIRECT`.

FLY **does not provide proxy nodes, VPS service, membership, accounts, payment, redemption codes or network service**.

## Why FLY

A normal proxy client can route traffic, but users often have to understand rule syntax, process matching and TUN behavior. FLY turns that into reusable profiles:

```text
PC
└─ FLY / Mihomo
   ├─ Granblue Fantasy / DMM / selected app -> FLY-JP
   └─ everything else                     -> DIRECT
```

The goal is **correct selective routing**, not promising that a poor-quality user node will become fast.

## v0.8 highlights

- **No full-browser proxying**
  - the old `full_browser` / `IN-PORT` catch-all is removed;
  - only explicit domain, keyword, IP/CIDR, process or port matches can use `FLY-JP`;
  - unmatched traffic always falls through to `MATCH,DIRECT`.
- **General routing profiles**
  - profiles are no longer conceptually limited to games;
  - built-in/community profiles live under `rules/`;
  - private custom profiles live under `private/custom_profiles.json`.
- **Automatic / Advanced / Custom modes**
  - **Automatic**: select profiles and let FLY choose a healthy Japanese node;
  - **Advanced**: use the same safe routing engine with manual node switching and node settings;
  - **Custom**: create/edit your own local JSON profiles without committing them.
- **Per-service node tests**
  - each profile can declare `latency_test_urls`;
  - when several profiles are selected, FLY tests candidate nodes against those service targets plus a generic fallback;
  - the conservative worst successful target is used for ranking.
- **Stable exit**
  - FLY remembers the last selected node and reuses it while it remains healthy.
- **Safer updater**
  - v0.8+ never downloads application code from the mutable `main` branch or a third-party mirror;
  - updates are discovered from GitHub Releases;
  - `FLY-update.zip` must have a matching `FLY-update.zip.sha256` release asset;
  - checksum mismatch or missing checksum causes the update to be skipped;
  - `private/`, `core/` and `runtime/` are never replaced.

## Routing selectors

A profile may contain any combination of:

- `domains`: domain suffixes, e.g. `example.jp`
- `keywords`: domain keywords, useful for CDN hostnames
- `ip_cidrs`: destination IP/CIDR ranges
- `processes`: Windows process names; this enables TUN
- `ports`: destination ports or port ranges
- `latency_test_urls`: endpoints used to evaluate candidate Japanese nodes

Example:

```json
{
  "id": "my-jp-service",
  "name": "My JP Service",
  "category": "Custom",
  "sort": 100,
  "launch_mode": "browser",
  "url": "https://example.jp/",
  "domains": ["example.jp"],
  "keywords": [],
  "ip_cidrs": [],
  "processes": [],
  "ports": [],
  "latency_test_urls": ["https://example.jp/"]
}
```

Old rule JSON files remain compatible. The legacy `full_browser` field is intentionally ignored from v0.8 onward.

### Process profiles

A profile containing `processes` uses Mihomo TUN and requires administrator rights. Process matching intentionally routes that process through the selected proxy group.

### Domain-only profiles

If no selected profile needs TUN, FLY temporarily points the Windows system proxy at the local Mihomo mixed port. This does **not** mean the browser is globally proxied: Mihomo still applies explicit profile rules and sends unmatched requests to `DIRECT`. The previous Windows proxy settings are restored when FLY stops and are also recovered after an abnormal exit.

## Installation

### Release build

Download `launcher.exe` from the latest GitHub Release and place it in an empty folder.

The launcher:

1. checks the latest GitHub Release;
2. applies an application update only when both `FLY-update.zip` and `FLY-update.zip.sha256` are present and match;
3. checks Python;
4. downloads Mihomo from the official MetaCubeX GitHub Release when the core is missing;
5. starts FLY.

### Source

Install Python 3.11+ and run:

```powershell
pyw main.py
```

For debug output:

```powershell
py -3 main.py
```

## Node source

FLY uses only nodes supplied by the user:

- a Clash/Mihomo subscription URL; or
- `private\nodes.yaml`.

Subscription URLs, UUIDs, credentials and local custom profiles are stored under `private/`, which is excluded by `.gitignore`.

FLY filters Japanese candidates using labels such as:

`Japan`, `JPN`, `JP`, `日本`, `Tokyo`, `Osaka`, `東京`, `大阪`, `🇯🇵`

It cannot guarantee that a provider labels nodes correctly, and it cannot turn a congested or poor route into a low-latency route.

## Built-in profiles

The repository currently includes profiles for:

- Granblue Fantasy
- 艦これ
- DMM / FANZA entry domains
- ウマ娘 (DMM版)
- NIKKE

The DMM profile is deliberately narrow in v0.8. It no longer sends every browser request through Japan just because DMM is enabled. Games hosted on third-party publisher/CDN domains should use their own profile or a user custom profile.

## Community profiles

To contribute a profile, add a JSON file to `rules/` and open a pull request. Keep rules as narrow as practical and do not use a catch-all rule.

Private experiments should go in the in-app Custom editor instead. They are saved to:

```text
private/custom_profiles.json
```

and are never committed by the normal repository configuration.

## Local caching

FLY v0.8 intentionally does **not** implement transparent HTTPS MITM caching for GBF or other services. Doing that safely would require certificate interception and substantially increase security and compatibility risk. Browser/application caches remain untouched.

Current optimization focuses on:

- selective routing;
- service-aware node testing;
- stable exit nodes;
- connection keep-alive;
- TUN/process routing where needed;
- QUIC fallback only for explicitly selected service domains.

## Privacy and security

- FLY does not operate an account backend.
- FLY does not upload your subscription URL or credentials.
- Mihomo's controller listens on `127.0.0.1` and uses a random local secret.
- App updates are release-versioned and SHA-256 verified.
- `private/`, `runtime/`, and `core/` are excluded from release source payloads.
- A routing profile that does not match traffic cannot proxy that traffic; the final rule is `MATCH,DIRECT`.

## Release packaging

`scripts\MAKE_RELEASE_ZIP.ps1` creates the source/update payload without `private/`, `runtime/` or `core/`.

For a v0.8+ self-update release, publish:

```text
FLY-update.zip
FLY-update.zip.sha256
```

The SHA-256 file may contain either the raw 64-character digest or standard `sha256sum`-style text.

The launcher executable can be built with:

```powershell
scripts\BUILD_LAUNCHER.ps1
```

## Disclaimer

This project is for learning and legitimate selective-routing use. Users must supply and operate their own lawful network access and comply with applicable laws and the terms of the services they access.

## License

Project code is released under the [MIT License](LICENSE).

Mihomo is a separate project distributed under its own license. FLY downloads the core from the official MetaCubeX release rather than bundling it into this repository.
