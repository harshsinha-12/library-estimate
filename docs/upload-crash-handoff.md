# Library Survey — upload crash handoff

**Date:** 2026-09-21  
**Device:** Harsh’s iPhone, iPhone18,1, iOS 27.0 (24A437), UDID `00008150-000244892647401C`  
**Bundle:** `dev.harshsinha.LibrarySurvey`  
**Team:** `PH4KQ4LY92`

## Symptom

On the Sealed Survey screen, **Test Connection** or **Upload or Resume** shows `Connecting to 192.168.29.178` (or a Local Network prompt), then the app **exits immediately**. Safari on the same phone can open `http://192.168.29.178:8000/healthz` and get `{"status":"ok"}`. Local Network is enabled for Library Survey. Mac and phone share Wi-Fi (`HarshJioFiber5G`). Phone LAN IP is `192.168.29.179`. Mac `en0` is `192.168.29.178`.

## Root cause (confirmed from device crash logs)

Copied from the phone via `devicectl device copy from --domain-type systemCrashLogs`.

Latest matching reports:

- `/tmp/iphone-crashes/LibrarySurvey-2026-09-21-115205.ips`
- `/tmp/iphone-crashes/LibrarySurvey-2026-09-21-114736.ips`

They are **not jetsam**. They are:

```
EXC_CRASH / SIGABRT
NSInvalidArgumentException
-[__NSCFNumber length]: unrecognized selector sent to instance
CFURLCreateStringByAddingPercentEscapes
initializeUserAgentString()
HTTPTransaction::_onqueue_prepareRequest
queue: com.apple.CFNetwork.Connection
```

Process lifetime is ~2 seconds from launch of the HTTP call to abort.

CFNetwork builds the default **User-Agent** on the first `URLSession` request. It expects `CFBundleVersion` to be a **string**. The built app had:

```
CFBundleVersion = 1   // integer / __NSCFNumber
```

Safari works because it does not use this app’s Info.plist. Uvicorn bind is unrelated: the Mac **is** listening on `0.0.0.0:8000` (`TCP *:8000`).

## Fix applied and verified

Upload and seal succeeded after the string `CFBundleVersion` fix. Survey `69d0a6d6-6ded-4280-bc07-eca057aa3f80` (Home) reached `status: geometry`. A later 422 on identical JSON at two paths was a second bug: idempotency keys are now `upload-{path}-{sha256}`.

- `ios/LibrarySurvey/LibrarySurvey/Resources/Info.plist`: `CFBundleVersion` is now `<string>1</string>`
- `ios/LibrarySurvey/project.yml`: `CFBundleVersion: "1"` (quoted so XcodeGen does not emit an integer)
- `backend/tests/test_ios_project.py`: asserts `CFBundleVersion` is a `str`
- Built app verified with `plutil`: `"CFBundleVersion" => "1"`

After this, the first HTTP request should not abort. Then Test Connection / Upload can actually reach the backend.

## Environment that is already working

| Check | Result |
| --- | --- |
| Uvicorn command | `python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000` as **one line** |
| Bind | `Uvicorn running on http://0.0.0.0:8000`, `lsof` shows `TCP *:8000` |
| Mac healthz | `curl http://127.0.0.1:8000/healthz` and `http://192.168.29.178:8000/healthz` → 200 |
| Safari on phone | `http://192.168.29.178:8000/healthz` → 200 (logged as `192.168.29.179`) |
| Local Network toggle | Settings → Privacy & Security → Local Network → Library Survey **ON** |
| App default URL | `http://192.168.29.178:8000` in `AppConfiguration.swift` and `@AppStorage("backendURL")` |
| ATS | `NSAllowsArbitraryLoads` + `NSAllowsLocalNetworking` + `NSLocalNetworkUsageDescription` + `_http._tcp` |

**Do not split `--port 8000` onto the next shell line.** That is `zsh: command not found: --port`. Default port is 8000 so a `--host 0.0.0.0`-only process can still look fine.

## How to install

```bash
cd "/Users/harshsinha/VS Code/library-roomplan/ios/LibrarySurvey"
xcodegen generate
xcodebuild -project LibrarySurvey.xcodeproj -scheme LibrarySurvey \
  -destination 'id=00008150-000244892647401C' \
  -allowProvisioningUpdates DEVELOPMENT_TEAM=PH4KQ4LY92 CODE_SIGN_STYLE=Automatic \
  -derivedDataPath /tmp/LibrarySurvey-dd build
plutil -p /tmp/LibrarySurvey-dd/Build/Products/Debug-iphoneos/LibrarySurvey.app/Info.plist | grep CFBundleVersion
# must print "CFBundleVersion" => "1"  (quotes = string)
xcrun devicectl device install app --device 00008150-000244892647401C \
  "/tmp/LibrarySurvey-dd/Build/Products/Debug-iphoneos/LibrarySurvey.app"
xcrun devicectl device process launch --device 00008150-000244892647401C \
  dev.harshsinha.LibrarySurvey
```

First `devicectl` launch after install often fails with “profile has not been explicitly trusted”; retry after 2s.

## Repro / verify plan

1. Confirm uvicorn is up as one line; `curl` healthz on the LAN IP.
2. Open Library Survey on the phone (restores last sealed package if `active-survey.json` still matches a package on disk).
3. Tap **Test Connection**. Expect “Backend reachable…” and a uvicorn log `GET /healthz` from `192.168.29.179`.
4. Tap **Upload or Resume**. Expect `POST /v1/surveys` then `POST /v1/surveys/{id}/uploads?path=...` for each file, then `POST .../seal`.
5. If it still exits, copy crash logs again:

```bash
xcrun devicectl device copy from --device 00008150-000244892647401C \
  --domain-type systemCrashLogs --source / --destination /tmp/iphone-crashes
ls -lt /tmp/iphone-crashes/LibrarySurvey-*.ips | head
```

If the new `.ips` still shows `initializeUserAgentString` / `__NSCFNumber length`, the installed Info.plist is still an integer (XcodeGen or `ProcessInfoPlistFile` rewrote it). If it shows jetsam, then memory is a second issue.

## Dead ends already tried (not the abort)

These were reasonable but **do not match the 11:52 SIGABRT**:

- RoomPlan / SceneKit / WKWebView / USDZ snapshot jetsam on the sealed screen
- `Data(contentsOf:)` of USDZ on the main thread (upload now uses `URLSession.upload(for:fromFile:)`)
- Local Network denied (user screenshot shows the toggle ON; Safari already worked)
- Uvicorn bound to `127.0.0.1` (it was `*:8000`)
- Split `--port` line (process still defaulted to 8000)
- Ping retries, ephemeral URLSession, NWBrowser local-network prompt

Streaming file hashes and releasing capture samples after seal are still useful. Hiding 2D/3D on the sealed screen was **not** required for the abort; those previews are restored (tagged plan + load-once SceneKit USDZ).

## Code to read first

| Path | Why |
| --- | --- |
| `ios/LibrarySurvey/LibrarySurvey/Resources/Info.plist` | `CFBundleVersion` must stay a string |
| `ios/LibrarySurvey/project.yml` | XcodeGen `info.properties` must quote `"1"` |
| `ios/LibrarySurvey/LibrarySurvey/Services/SurveyUploadService.swift` | First HTTP is `ping` → `GET /healthz` or `upload` → `POST /v1/surveys` |
| `ios/LibrarySurvey/LibrarySurvey/Views/PackagePreviewView.swift` | Sealed/upload screen: tagged 2D plan + load-once USDZ |
| `ios/LibrarySurvey/LibrarySurvey/Export/FloorPlanLayout.swift` | Numbered colored walls, cm, openings, compass N = scan +Z |
| `backend/app/workflows/floor_plan.py` | Server-side tagged `derived/plan.svg` + geometry summary; sealed `generated/plan.svg` remains immutable |
| `backend/app/api/routes.py` | `POST /v1/surveys`, `POST .../uploads` (`await request.body()` loads whole file on the Mac), `POST .../seal` |
| `backend/app/main.py` | `GET /healthz` |
| `INSTALLATION.md` | Cable install + one-line uvicorn |

## Upload API (once the crash is gone)

1. `POST /v1/surveys` JSON `{survey_id, display_name, geography}` + `Idempotency-Key`
2. For each manifest file: `POST /v1/surveys/{id}/uploads?path=` with file body + `Content-Type` + `Idempotency-Key: upload-{path}-{sha256}` (path is required; two files can share bytes)
3. `POST /v1/surveys/{id}/seal` with the capture manifest JSON

iOS encodes keys with `convertToSnakeCase`. Geography `city` must be non-empty; lat/lon require `precise_location_consent`.

## Product context (do not drop)

Stage-1 library survey: RoomPlan capture on device → hashed capture package → FastAPI ingest. Broader plan in `FINAL-PLAN.md` / `IMPLEMENTATION.md`. No Amazon scrape. Bing later for prices. Fable/Astra model split stays. Personal Apple team `PH4KQ4LY92`.
