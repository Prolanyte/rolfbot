# YouTube audio playback feasibility

**Research date: 14 October 2026 (Europe/Oslo).** This is a documentation review,
not a playback report. No live extraction, streaming, authenticated account
access, PO Token provider setup or VM access was performed. Sources are mutable;
recheck them when implementing. An older publication date is identified where
relevant rather than presented as a new October result.

## Recommendation

Retain music as an **optional, conditional milestone**. `yt-dlp` is a reasonable
replacement candidate for `youtube_dl`, but reliable unattended YouTube audio on
the intended Vultr VM is **unproven and high risk**. Complete AI/Discord
modernization independently, and leave music disabled until the testing and
policy gates below pass. See the [roadmap](ROADMAP.md).

The upstream [YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors)
says default clients can avoid some PO Token requirements, with reduced format
availability; it recommends a token-backed `mweb` route when defaults fail.
That is configuration guidance, not evidence that this bot or its host works.

In [issue #17103](https://github.com/yt-dlp/yt-dlp/issues/17103), opened 30 June
2026, maintainer bashonly's pinned 9 July comment identifies datacenter IPs,
IPv6, VPN/proxy use, outdated builds, extractor overrides and high volume as
circumstances associated with HTTP 403 failures. The issue was closed as not
planned, not as a universal fix. Reports in the thread also describe residential
failures, but those reports are not controlled reliability measurements.

## Dependencies and version selection

The current [yt-dlp README](https://github.com/yt-dlp/yt-dlp#dependencies) lists
CPython 3.10+ and PyPy 3.11+ support and recommends FFmpeg, ffprobe, EJS scripts
and a JavaScript runtime. FFmpeg means the actual system executable, not a Python
package with that name. No versions have been installed, pinned or tested here.

| Component | Purpose and future selection |
| --- | --- |
| `yt-dlp` | Extract metadata and an available audio format; select and record a tested release |
| FFmpeg / ffprobe | Decode/transcode audio and inspect formats; record actual executable paths and versions |
| `yt-dlp-ejs` | Companion scripts used to solve YouTube JavaScript challenges |
| JavaScript runtime | Execute the EJS challenge solver; required in addition to its scripts |
| `discord.py` voice stack | Send encoded voice to Discord; select its supported voice extras and native dependencies |

The [EJS setup guide](https://github.com/yt-dlp/yt-dlp/wiki/EJS), edited 12 July
2026, gives the following compatibility guidance:

| Runtime | Upstream status at research time |
| --- | --- |
| Deno | Recommended; enabled by default; minimum 2.3.0 |
| Node.js | Supported from 22.0.0; explicitly enable it |
| QuickJS / QuickJS-NG | Supported; older builds can be slow and use temporary script files |
| Bun | Deprecated; versions newer than 1.3.14 unsupported |

For pip installations, `yt-dlp[default]` includes `yt-dlp-ejs`; keep it compatible
with the selected yt-dlp release. Bundled executables and distribution packages
have different inclusion rules. EJS can also be fetched remotely, but the
proposed service should use reviewed dependencies provisioned during deployment,
not unreviewed runtime downloads. Deno's restricted execution is a useful reason
to prefer it. EJS challenge solving and PO Token generation are separate tasks.
[EJS installation and runtime notes](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

Discord's [installation guide](https://discordpy.readthedocs.io/en/stable/intro.html)
specifies the `discord.py[voice]` extra and Linux native prerequisites. Check the
resolved voice dependencies, including PyNaCl and
[Opus support for a PCM path](https://discordpy.readthedocs.io/en/stable/api.html#discord.VoiceClient),
in the actual environment. The [changelog](https://discordpy.readthedocs.io/en/stable/whats_new.html)
records DAVE voice encryption support in 2.7.0 and missing-`davey` diagnostics in
2.7.1. Those are observed release notes, not tested version pins for Rolfbot.
Validate the complete current voice stack rather than assuming old examples
remain compatible.

## PO Tokens and provider plugins

A Proof of Origin token attests a client request. Requirements depend on the
YouTube client and request type, including player metadata, media-server (GVS)
access and subtitles. Tokens are **not universally mandatory for every request**,
but some formats fail without them. The upstream recommendation is a provider
plugin supplying GVS tokens for `mweb`; copying a single token is fragile because
tokens can be video-bound. A provider hooks into the extractor to obtain the
needed token from an attestation implementation, browser or helper service.
[PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).

The guide lists `bgutil-ytdlp-pot-provider` and `yt-dlp-getpot-wpc`, including
maintainer involvement, while explicitly identifying them as unaffiliated
plugins. They introduce code/dependency trust, resource costs and another moving
part. Their presence does not establish successful media access. Do not configure
either in Phase 0 or require one silently in the eventual bot.
[Provider plugin guidance](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide#po-token-provider-plugins).

The upstream [known-issues tracker](https://github.com/yt-dlp/yt-dlp/issues/3766)
warns that a provider will not repair an IP that is already blocked. A working
JavaScript challenge solver or token-generation log is therefore insufficient
evidence of successful extraction, much less audible Discord playback.

## Failure modes and operational response

These are proposed Rolfbot responses, not implemented recovery behavior.

| Symptom | Evidence or interpretation | Proposed response |
| --- | --- | --- |
| HTTP 403 | May reflect client/token restrictions, IP reputation or stale extraction state; one code does not identify the cause | Record sanitized stage/error details; allow at most one fresh extraction for suspected expiry, then fail clearly |
| HTTP 429 / temporary unavailability | Upstream documents throttling and IP/session overuse | Stop repeated attempts, use bounded backoff and respect service retry guidance |
| Sign-in / bot challenge | Can indicate a blocked anonymous IP | Disable the source temporarily; do not automatically import browser cookies |
| Missing audio formats / JS challenge failure | Runtime or EJS mismatch, changed client formats, restrictions or extractor breakage | Check versions and dependencies; distinguish metadata success from usable audio |
| Expired media URL | Treat signed media URLs as temporary, not durable queue entries | Store the original video reference; resolve near playback; never persist credentials in signed URLs |
| FFmpeg/voice failure after extraction | Media headers/protocol/codec, voice dependencies or network connectivity may differ | Diagnose media reading and Discord voice separately; clean up all workers and processes |

The [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#http-error-429-too-many-requests-or-402-payment-required)
connects HTTP 429 with service blocking due to overuse. The
[extractor guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#common-youtube-errors)
also documents guest/account request throttling. Their illustrative rates are
not promised quotas for Rolfbot. Use a small queue, serialize extraction initially,
and stop on repeated restrictions rather than probing aggressively.

The [upstream update guidance](https://github.com/yt-dlp/yt-dlp#update) recommends
nightly releases for regular users because stable builds can lag site changes.
Proposed policy: diagnose against a current upstream-supported build in staging,
record the chosen version, test it, then promote it deliberately. Do not
self-update a running production service or treat a new build as proof of a fix.

## Vultr and datacenter hosting

The intended Vultr host has a datacenter IP, putting it within the risk category
identified in [the maintainer's #17103 guidance](https://github.com/yt-dlp/yt-dlp/issues/17103).
**Inference:** it can fail where a home connection succeeds, even at Rolfbot's
low usage. This is not evidence of a Vultr-wide ban or a diagnosis of the owner's
specific VM. Its actual IP reputation, IPv4/IPv6 behavior, routing and available
CPU/memory have not been tested.

Repeat the same permitted samples with the same software on local development
and, only later with authorization, the VM. Compare normal egress and IPv4 where
available as a diagnostic, not a guarantee. Do not build the design around paid
proxies or IP rotation. If the host consistently encounters restrictions, choose
an alternative source or disable music. Measure impact on the existing nginx
website before enabling parallel voice playback.

## Accounts, cookies and policy

Authenticated cookies are credentials. Upstream warns of temporary or permanent
account bans, cookie rotation, and limitations of account-based access; its
extractor guide also says OAuth login is no longer supported. The initial
proposal is public, permitted content without authenticated accounts. Cookies
would require an explicit later owner decision, protected storage and redacted
diagnostics; they are not a reliable cure for IP restrictions.
[YouTube account guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

YouTube's [Terms of Service](https://www.youtube.com/static?template=terms)
restrict automated access and downloading/retransmitting content except as
authorized, and restrict interfering with access controls. Playback in a small
private server does not itself establish permission. Separately, the
[YouTube API developer policies](https://developers.google.com/youtube/terms/developer-policies)
restrict separating audio/video and background players for API clients; an
official API key is not an authorization for arbitrary audio relay.

Before enabling the feature, the owner must review the terms applicable in their
region and establish permissions for the intended content/use. The terms page
retrieved here is dated 15 December 2023; the Norway-specific URL could not be
retrieved, so this is not confirmation of every regional provision. Copyright
permission and platform permission are distinct. No permanent download does not
remove these considerations. Do not design bypasses for DRM or account/region
restrictions. Where permission is unclear, prefer authorized sources or links
opened in YouTube's own player.

## Streaming design to evaluate later

**Feasible in principle, unverified here:** extract a usable audio URL and its
required request context, have FFmpeg read it, and feed decoded PCM or suitable
Opus audio into the Discord voice client. This can avoid permanently storing
media; bounded memory/pipe buffering and temporary runtime files may remain.
FFmpeg supports network input, protocol restrictions, I/O timeouts and bounded
reconnection controls. [FFmpeg protocol documentation](https://ffmpeg.org/ffmpeg-protocols.html).

This is an architecture proposal, not a claim that every yt-dlp format works as
an FFmpeg URL. Some formats need manifests, special protocols or request context
that cannot be handed over trivially. Test format compatibility and preserve
only the required trusted headers. Use the same approved egress context for
extraction and playback. A pipe from a supervised media worker is another option
to evaluate if direct URL handoff fails; it still depends on permitted access.

### Security and resource controls

These are requirements for future implementation:

- Accept only canonical, allowlisted HTTPS video URLs initially. Reject arbitrary
  hosts, userinfo, local paths, IP literals and unexpected ports; reconstruct a
  canonical video URL from validated input rather than passing arbitrary text.
- Validate redirects, DNS results and extracted media destinations too. Block
  private, loopback, link-local and cloud-metadata addresses, including IPv6.
  A hostname check alone is insufficient against redirect/DNS changes; enforce
  network restrictions at connection time where possible.
- Pass subprocess arguments without a shell. Never interpolate user input into
  executable names, flags, output templates or headers. Restrict FFmpeg protocols;
  its [protocol allowlist](https://ffmpeg.org/ffmpeg-protocols.html#Protocol-Options)
  controls protocols, not destination IP safety.
- Isolate extraction and JS processes from bot secrets and website files. Audit
  plugins before installation, keep dependency provenance/version records, and
  avoid loading personal yt-dlp config, cookies or arbitrary plugin directories.
- Bound track length, queue length, concurrent workers, extraction time, network
  reads, process count and memory/CPU. Stop FFmpeg/JS workers on cancellation,
  disconnect and shutdown; clean temporary files on failures and restart.
- Redact cookie/token headers, signed URL query strings and proxy credentials
  from logs and error messages. Send friendly Discord errors instead of raw
  extraction diagnostics. Test redaction before sharing troubleshooting logs.

## Future testing plan — not performed

Begin only in a separately authorized implementation/testing phase. Use a small
set of public clips owned by the tester or otherwise permitted for this purpose;
do not exercise private, age-restricted, DRM-protected or paid content. Start
without cookies, providers or proxies. The VM steps need separate production
access authorization and must preserve the existing website.

| Stage | Future test | What it establishes |
| --- | --- | --- |
| 1. Local inventory | Record Python, yt-dlp, EJS, JS runtime, FFmpeg/ffprobe and Discord voice versions; inspect paths/dependency detection | Whether the required stack is present; proves no extraction success |
| 2. Offline validation | Mock hostile URLs/redirects/DNS, extractor outputs, timeouts, queue saturation and cleanup | Whether local security and lifecycle policies work without network |
| 3. Local extraction | Resolve several permitted finite clips at low rate; record selected audio format, elapsed time and warnings | Whether metadata and audio selection work for those clips/network/time |
| 4. Actual media read | Have FFmpeg read/decode a short bounded audio segment to a discard sink without permanent media storage | Whether the returned media is actually accessible/decodable, beyond format listing |
| 5. Local Discord voice | Play a permitted short clip in a designated test channel; confirm audible output, stop, skip and idle disconnect | End-to-end voice compatibility, including encryption and codec handling |
| 6. Repeat/expiry | Repeat playback across at least two sessions on different days; simulate stale URLs and then test delayed playback with fresh extraction | Short-term reproducibility and recovery; not a long-term availability guarantee |
| 7. Vultr comparison | After authorization, use the same versions and clips on the VM; compare ordinary/IPv4 egress if available | Whether this host differs from local development; does not isolate IP reputation conclusively |
| 8. Coexistence | Measure CPU, memory, workers, outbound traffic and website latency under up to two voice sessions | Whether the small VM can serve both guilds while preserving nginx headroom |
| 9. Recovery | Simulate 403/429, provider unavailability if applicable, voice disconnect, restart and cancellation | Friendly failure, bounded retries and no leaked processes/files |
| 10. Decision | Record results and limitations; optionally assess a reviewed provider only after a separate owner decision | Evidence for enabling music, further investigation or choosing a fallback |

Store dates, versions, environment, attempt counts, stage-specific outcomes,
startup latency and peak resource usage in a future test report. Keep private
network details and secrets out of public logs. Proposed release gate: ten
completed permitted short-track playbacks across the two sessions **on each
environment**, plus successful recovery/cleanup and acceptable website headroom.
Agree on the performance ceiling before testing. Any persistent host restriction
or unresolved permission issue blocks enabling YouTube, regardless of local
success. Stop testing on throttling; do not generate load to discover a limit.

## Alternatives and risk assessment

| Alternative | Benefit | Remaining limitation |
| --- | --- | --- |
| Rights-cleared local audio | Predictable media access; no YouTube extraction | Storage, content permissions and the Discord voice stack still matter |
| Approved direct audio/radio URLs | Can simplify extraction | Must permit relay; URL, protocol and resource controls still required |
| Link sharing in Discord | Minimal maintenance; playback stays in the provider's player | No bot-mediated voice playback |
| Music disabled at first release | Keeps AI modernization independent | Historical music capability remains unavailable |

An external music backend can move extraction work but does not inherently
resolve YouTube restrictions or grant content rights. Other subscription music
APIs likewise should not be assumed to authorize Discord rebroadcasting.

| Risk | Assessment | Mitigation / remaining uncertainty |
| --- | --- | --- |
| Vultr extraction reliability | High | Actual host tests required; maintain a fallback |
| Upstream changes | High maintenance risk | Tested updates, pinned releases and a music-disable switch |
| Platform/content permissions | Release gate | Owner verifies applicable terms and rights before enabling |
| Account/cookie exposure | High if authentication is introduced | No cookies initially; separate review if later necessary |
| Untrusted URLs and subprocesses | High impact | Restricted inputs/egress, isolation and adversarial tests |
| VM resource contention | Unknown | Profile two-guild voice use and enforce measured limits |

The evidence supports an experimental, optional feature with bounded failure
handling. It does **not** support promising reliable YouTube playback today.
