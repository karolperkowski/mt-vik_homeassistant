# Project Logbook

Running log of progress, decisions, and open work for the MT-VIKI HDMI matrix
Home Assistant integration. Newest entries first. Update this file whenever a
work session lands something or a decision is made.

## Status at a glance

| Area | State |
|---|---|
| Integration (HACS-installable) | ✅ complete, unvalidated on hardware |
| Test suite | ✅ 150 passing (mock-based) |
| CI (lint, pytest, HACS, hassfest) | ✅ all green |
| Releases | v0.1.0 · v0.2.0 · v0.3.0 (tag-driven pipeline; v0.1.0 tag/release lost in history rewrite — see 2026-08-24 janitorial entry) |
| **Hardware validation** | ⬜ **blocked — no physical unit yet** |
| HACS default store + brands | ⬜ deferred until hardware-validated |
| Serial/RS-232 transport | ⬜ deferred until hardware available |
| Passive discovery (DHCP/OUI) | ⬜ needs MAC/OUI from real unit |

## Log

### 2026-08-25 — Attribution purge: repo recreated; guards installed
- Claude co-author trailers were still publicly visible despite the earlier
  history scrub: GitHub keeps PR refs (`refs/pull/*`) forever, and the two
  pre-rewrite Dependabot PRs preserved the original trailered commits.
  Live history, contributors API, and sidebar were already clean.
- Fix chosen (user decision): **deleted and recreated the repo** (0 stars,
  0 real issues, 0 secrets — only the tainted PRs were lost). Same
  name/description/topics → same URL; pushed clean main + all tags.
- Landmine: pushing the three tags in one push triggered **no** release
  workflows (no runs, no releases). Releases were recreated directly via
  `gh release create --verify-tag --generate-notes` (each tag's content had
  already passed the verify pipeline); v0.3.0 marked Latest. When restoring
  tags in future, push them one per push or expect to create releases by
  hand.
- Verified end state: contributors = owner only; zero `refs/pull/*`; old
  trailered SHAs return "no commit found"; releases v0.1.0–v0.3.0 present,
  v0.3.0 Latest.
- Prevention (defense in depth): user-level Claude Code `attribution`
  settings (no trailer generation anywhere), global `~/.githooks/commit-msg`
  hook (rejects attributed commits machine-wide, tested both paths), and an
  `attribution-guard` CI job in tests.yml + release.yml (blocks any push or
  release whose history contains attribution — green on first run). The
  /tidy skill now audits history and PR refs for attribution in any repo.

### 2026-08-24 — Janitorial pass: drift audit + session workflow rules
- Drift audit found the **v0.1.0 tag missing** both locally and on GitHub —
  lost in the history rewrite (tags point at pre-rewrite hashes) — while
  CHANGELOG/LOGBOOK still referenced it. Original tag point identified as
  `207437c` (manifest 0.1.0 + changelog with only the 0.1.0 section); tag
  recreated **locally only**. Pushing it would trigger release.yml and the
  new release would be marked "Latest" — if restoring it on GitHub, push the
  tag, then `gh release edit v0.3.0 --latest`.
- Fixed CHANGELOG link refs (stale: `[Unreleased]` compared from v0.1.0, no
  0.2.0/0.3.0 refs; 0.1.0-era links now commit-based since the public tag is
  gone). Added `.ruff_cache/` to .gitignore; removed local `__pycache__`.
- Verified doc claims against reality: 150 tests pass, ruff lint+format
  clean, hacs/manifest/workflows consistent, README accurate (one known
  screenshot placeholder for the crosspoint card remains).
- Added **session workflow rules** to CLAUDE.md (mirrored in Claude's global
  memory): drift check at the start of any codebase-touching session;
  every new rule or caught error class is immediately backported across the
  existing repo; multi-part work is parallelized via Opus subagents given
  fully-specified plans.

### 2026-08-24 — Cards, naming, releases
- Cut **v0.3.0**: crosspoint Lovelace card (`www/mtviki-matrix-card.js`),
  served and auto-registered by the integration; actions bumped to
  checkout@v7 / setup-python@v7.
- Cut **v0.2.0**: network-scan discovery in the config flow (SWS-reply
  fingerprint), `mtviki_matrix_route_changed` bus events, input/scene naming
  via options flow, current-scene sensor, release automation + Dependabot.
- Rewrote git history to strip all Claude attribution trailers (standing
  rule: none on future commits), force-pushed; repo made public; HACS +
  hassfest validation green.
- Note: an Anthropic API incident (529s) interrupted the build agents for
  ~30 min; work resumed on fallback capacity, no impact on output.

### 2026-08-23 — Initial build (no hardware)
- Repo started empty. Protocol sourced entirely from
  bitfocus/companion-module-mt-viki-matrix (MT-HD0808, ASCII over TCP 8080);
  key facts: `\r\n` commands, unsolicited `SWS` pushes (→ `local_push`),
  1-based ports, no NAK — silence is the only failure signal.
- Built: vendored asyncio client (`api.py`), config flow, push-first
  coordinator, all entity platforms, 6 services, diagnostics, probe CLI
  (`mtviki_probe.py`), 94-test suite with stateful mock matrix, HACS
  packaging, README/PROTOCOL docs, ruff config (71 findings fixed), CI.
- Released **v0.1.0**.

## Next session: hardware validation (when the unit arrives)

Work through `PROTOCOL_VALIDATION.md` end-to-end using `mtviki_probe.py`:

1. `python3 mtviki_probe.py --host <ip> scan` — confirm port 8080 speaks the
   protocol (fallbacks: 5000, 23, 4001; then RS-232 115200 8N1).
2. `probe --md PROTOCOL_VALIDATION.md` — read-only sweep, log all replies.
3. The four open questions (each section ends in a which-file-to-change verdict):
   - **BeepONOnce vs BeepEn gating** → affects `async_locate()` in `api.py`
   - **HDCP mode values** (0/1/2/3 = off/1.4/2.0/2.2 vs 0/1/2 = off/on/follow)
     → affects HDCP select in `select.py` + translations
   - **Valid SetEDID select values** (currently unvalidated 1–16)
   - **SetEDIDData payload format** (assumed 512 hex chars)
4. Record MAC/OUI + DHCP hostname + UDP:4000 broadcast behavior (§13) →
   enables passive discovery work.
5. Then: brands PR + HACS default-store submission; consider serial transport.
