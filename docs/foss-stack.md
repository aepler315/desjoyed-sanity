# FOSS replacement stack

Install these **before** enabling the matching debloat tier.
All are available on F-Droid or via Obtainium from their GitHub releases.

## Essentials (install before google.txt)

| Replaces | Install | Source |
|---|---|---|
| Gboard | **HeliBoard** or FlorisBoard | F-Droid |
| Google TTS | **RHVoice** or eSpeak NG | F-Droid |
| Google Maps | **CoMaps**, Organic Maps, or OsmAnd | F-Droid |
| Play Store | **F-Droid** + Obtainium; Aurora Store for Play apps | f-droid.org |
| Chrome | **Fennec** (Firefox) or Mull | F-Droid |

Switch to the new keyboard and TTS engine in Settings before you disable
the Google ones. The active input method and TTS engine are protected by
Android; the old APKs are just dead weight once you switch.

## Nice to have

| Category | App | Notes |
|---|---|---|
| Launcher | Lawnchair | Set as default home BEFORE disabling the stock one |
| File manager | Material Files | Replaces Files by Google; keep DocumentsUI (protected) |
| Music | Auxio, Vinyl | Local-file players |
| Video | VLC, mpv-android | |
| YouTube | NewPipe or Tubular | Tubular has SponsorBlock |
| Firewall | NetGuard (no root) or AFWall+ (root) | Per-app network control |
| DNS | Private DNS in Settings (no app needed) | dns.quad9.net, dns.adguard-dns.com |
| Network monitor | PCAPdroid | See what talks where |
| App store | Obtainium | Direct from GitHub releases |
| Calendar | Etar | |
| Contacts | Simple Contacts Pro | Fossify fork |
| Gallery | Aves, Simple Gallery | |

## microG (the middle ground)

If you disable Google Play services (com.google.android.gms) but still
need push notifications or location via the network, install microG:

1. Magisk module: microG Installer Revived (GitHub)
2. Or the standalone microG APKs from microg.org

microG re-implements the Play services API locally. Maps SDK, push
(GCM/FCM), account auth, and SafetyNet/Play Integrity all work to
varying degrees. No Google account is required for push; you do need
one for apps that gate on sign-in.

## Order of operations

1. Install F-Droid + Obtainium
2. Install keyboard, TTS, browser, maps, launcher
3. Switch to the new keyboard and TTS in Settings
4. Set the new launcher as Home (sanity.py home --launcher <pkg>)
5. Set private DNS (sanity.py dns)
6. Now enable the google.txt tier
