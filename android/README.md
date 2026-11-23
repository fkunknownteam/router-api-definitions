# How the Android app stays generic

The app ships **no vendor endpoints**. It ships a definition loader and an
executor. Everything a model can do — paths, field names, encodings, cipher
parameters, login flow, which features exist — arrives at runtime inside
`api.json` and `capabilities.json`.

    app start
      └─ GET registry/index.json                    (cached, TTL 24 h)
           └─ user enters IP + password
                └─ read the firmware string from the device
                     └─ pick the variant whose matchPrefix fits
                          └─ GET api.json + capabilities.json
                               └─ verify sha256 from the index
                                    └─ cache by hash, then execute endpoints by id

## 1. Refresh the index

One request, small, cacheable. Keep the previous copy and use it when the
network is down; a router LAN usually has no internet.

```kotlin
val index = DefinitionRepository.fetchIndex()   // GET registry/index.json
```

Do not hardcode the raw URL in more than one place. One constant:

```kotlin
const val REGISTRY_BASE = "https://raw.githubusercontent.com/fkunknownteam/router-api-definitions/main/registry"
```

## 2. Identify the device before downloading a definition

The definition tells the client how, so this step is data-driven too. Two
usable signals and their limits:

| Signal | Where | Caveat |
|---|---|---|
| `adv_firm_ver` | `status.system`, after login | needs a session, and the cipher differs per family, so a wrong guess fails at decryption |
| `deviceName` / model string | `status.router`, after login | not present on every build |

Because both need a session, probe in this order and stop at the first hit:

1. Fetch the index, group variants by `matchPrefix`.
2. For each candidate whose `hardwareVersions` the user selected (or all of
   them), try login with the credentials already entered.
3. Read the declared `identify.endpoint`. Compare `identify.field` with
   `identify.equals`, and the firmware string against `matchPrefix`.
4. First full match wins. Cache the choice against the router's MAC so the next
   launch skips probing.

A login that fails at the AES step is not a wrong password: if the definition
declares `crypto` and the reply is base64 that will not decrypt, that variant is
the wrong dialect. Move to the next candidate instead of reporting a bad
password to the user.

## 3. Download and verify

```kotlin
val variant = index.select(firmware = "V15.03.06.50_multi", model = "AC10")
val apiBytes = http.get(variant.apiUrl)
require(sha256(apiBytes) == variant.sha256.api) { "definition corrupt" }
val capsBytes = http.get(variant.capabilitiesUrl)
require(sha256(capsBytes) == variant.sha256.capabilities) { "capabilities corrupt" }
```

Store both files under `filesDir/definitions/<api-sha256>/`. Cache key is the
hash, so a new definition never collides with an old one and rollback is just
pointing at the previous hash.

## 4. Gate the UI on capabilities, not on the model name

```kotlin
if (caps.supports("wifi.guest")) showGuestCard()
if (caps.supports("wifi.guest", write = true)) enableGuestSaveButton()
```

Rules, and they matter:

* value `full` → read and write exist
* value `read-only` → show the data, disable the control, explain why
* value `unknown` → show the control but expect failure; never claim the router
  cannot do it
* key **absent** → the definition proves the device cannot do it; hide the
  control

Never write `if (model == "AC10")`. The moment that appears, the app is no
longer generic and a new model needs an app release.

## 5. Execute

Every call goes through one function that reads the definition:

```kotlin
val body = definition.buildBody("wifi.basic.set", values)   // encrypted-form if declared
val reply = client.post(definition.path("wifi.basic.set"), body)
```

Three things the executor must honour, all declared in the file:

* `crypto.keySource` with `perRequest: true` — fetch the key immediately before
  the request it protects. Never reuse the key from login or a previous call.
* `envelopes.resultField` — treat `0` as success and any other value as a
  device-level refusal to surface, not as a parse error.
* `envelopes.nonJsonEndpoints` — these answer with a redirect or HTML. Do not
  parse them as JSON, and do not log their bodies as errors.

Writes: check `writes`, `risk` and `requiresConfirmation` before sending. A
`destructive` endpoint gets a confirmation dialog; `medium` and above should
name what will change ("clients on 5 GHz will disconnect").

## 6. What the app must never do

* Invent a field the definition does not declare. An omitted field on these
  firmwares is often a reset to default, which silently breaks the network.
* Treat a failed getter as "feature absent". Use the capabilities file.
* Ship a per-model screen. One screen, driven by `endpoint.feature`.
* Cache a decrypted reply as if it were the definition. Replies are per-device
  state; definitions are per-firmware contract.

## Reference implementation

`android/RouterEngine.java` is a working loader and executor in plain Java: no
dependencies at all, no Android APIs beyond `java.net` and `javax.crypto`, so it
compiles and runs on a desktop JDK as well as inside the app. It is deliberately
small — the shape the app needs, not a library.

It is exercised for real: `tools/java_selftest.sh` starts the fake router and
drives every definition through the Java engine, so the Android path is tested
against the same documents the Python client is. See `docs/verification.md` for
what that does and does not prove.

`android/definition-cache.md` describes the on-disk layout and the offline
behaviour.
