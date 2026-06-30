# Native Login — Replicating the Polyconnect Auth Protocol

This document explains how the Polyconnect mobile app authenticates against
`auth.pool.mytech-connect.io`, and how we replicate that wire format in pure
Python so the HA add-on can log in directly with email + password (no
mitmproxy capture step).

The POC is in [`scripts/login_poc/polyconnect_login.py`](../scripts/login_poc/polyconnect_login.py).
All file references below point into [`polyconnect_apk/decompiled_src/`](../polyconnect_apk/decompiled_src/)
which is the JADX/ILSpy decompile of the .NET MAUI app shipped in the APK.

---

## 1. What the v1 add-on does today

The v1 add-on **never logs in**. The setup wizard tells the user to:

1. Install the mitmproxy CA cert on their phone.
2. Route the phone through the add-on's mitmproxy (`scripts/capture/mitm_addon.py`).
3. Open the Polyconnect mobile app and log in.
4. The proxy captures the `GET https://polytropic.user-app.pool.mytech-connect.io/from-native/<token>`
   request the app's webview fires after login. The base64 blob in the URL path
   is saved as the addon's "token".

The bridge (`polyconnect_bridge/server.py`) then drives Playwright against that
captured `/from-native/<token>` URL to control the heat pump. Login is treated
as a black box.

This is awful UX (CA install, phone proxy, re-capture every time the session
expires), so v2 replaces it with native login.

---

## 2. The actual mobile-app login flow

`BlazorApplicationService.Login()` (`IngeliStdMaui/IngeliStdMaui.Services/BlazorApplicationService.cs`)
runs the following two-step flow.

### Step 1 — register a terminal

The first time the app launches, it generates a device fingerprint and POSTs it
to `/Irc/Terminal/RegisterTerminal`. The server returns a `terminal_id`
(MongoDB ObjectId) plus a `terminal_transaction_key` (a server-issued secret
used to sign every subsequent request from this device).

```
ApplicationRemoteClient → TerminalApiRemoteClient (PublicTransactionApiRemoteClient)
  POST /Irc/Terminal/RegisterTerminal
  body = public-signed envelope wrapping
         { "terminal": { Manufacturer, Model, OperatingSystem, OperatingSystemVersion, TerminalPrint } }
  response = public-signed envelope wrapping
             { s: 100 /* Success */, ti: "<terminal_id>", ttk: "<terminal_transaction_key>" }
```

`TerminalPrint` is `Base64Url(SHA256(device_uuid))` — opaque to the server, just
needs to be a stable string per device.

### Step 2 — login with credentials

```
ApplicationRemoteClient (TerminalTransactionApiRemoteClient)
  POST /Irc/Application/Login
  body = terminal-signed envelope wrapping
         { "args": {
             aeid: "userApp_polytropic",
             e:    "<lowercased trimmed email>",
             h:    "<password hash>",
             tid:  "<terminal_id>",
             av:   "<app version>",
             pn:   "<package name>",
           }}
  response = terminal-signed envelope wrapping
             { s: 0 /* AuthenticationResult.Success */,
               t: "<token>",
               url: "https://polytropic.user-app.pool.mytech-connect.io/from-native" }
```

The login result's `(url, token)` pair is the **same** `/from-native/<token>`
URL the v1 capture flow extracts via mitmproxy. The mobile app loads it in a
webview (`BlazorNativeWebView.LoadApplication` in
`IngeliStdMaui/IngeliStdMaui.Views.HybridWebViews/BlazorNativeWebView.cs:26`):

```csharp
base.Source = new UrlWebViewSource { Url = url + "/" + token };
```

So once we replicate steps 1+2 in Python, we get the exact bootstrap URL the
existing Playwright bridge already knows how to drive.

---

## 3. Constants

All extracted from `PolyconnectUserAppMaui/PolyconnectUserAppMaui/MauiProgram.cs`:

| Constant                  | Value                                                | Used for                              |
|---------------------------|------------------------------------------------------|---------------------------------------|
| `ApplicationAuthenticationUrl` | `https://auth.pool.mytech-connect.io`           | Base URL for both endpoints           |
| `ApplicationEndpointId` (aeid) | `userApp_polytropic`                            | Login arg                             |
| `PublicTransactionKey`    | `ZZuo8EMfc93KtDU745gvzw8DsWY0`                       | Sign RegisterTerminal request         |
| `PreSalt`                 | `zLT6DV`                                             | Password hash prefix                  |
| `PostSalt`                | `NEEJ9S`                                             | Password hash suffix                  |

> **Side note:** the v1 bridge uses these exact same salts as `CF-Access-Client-Id` /
> `CF-Access-Client-Secret` HTTP headers (`polyconnect_bridge/server.py:42`). They
> are pre-baked Cloudflare Access tokens that gate the user-app domain; we leave
> them as-is because the bridge already needs them to fetch the Blazor SPA.

### Password hash

`BlazorApplicationService.HashPassword`:

```csharp
sha256_hex(PreSalt + password + PostSalt)
```

Plain unsalted-but-peppered SHA-256, hex-lowercase. Same code in our Python
POC, ~3 lines.

---

## 4. The signed-transaction envelope

Every request body sent to the auth API is wrapped in the **same envelope** —
`PublicSignedTransaction` for terminal registration (signed with the public
key) or `TerminalSignedTransaction` for everything afterwards (signed with the
terminal's per-device key).

```
{
  "tpv": 1,                  // TransactionProtocolVersion.Version1
  "psp": "<sig>.<encoded>",  // signature + "." + encoded protected payload
  "tid": "<terminal_id>"     // ONLY in TerminalSignedTransaction
}
```

The interesting work is inside `psp`. Reference:
`IngeliStdSecurity/IngeliStdSecurity.Transaction/TransactionSecurityProviderBase.cs`
methods `GeneratePublicTransaction` and `GenerateTerminalTransaction`.

### Inner payload

The actual JSON body (e.g. the `TerminalRegisterTerminalArgs` or
`ApplicationLoginArgs` object) is serialized with Newtonsoft.Json using the
default contract resolver (PascalCase property names), with:

- `NullValueHandling = Ignore` — drop `null` fields
- `DateFormatString = "yyyy-MM-ddTHH:mm:ss"` — no tz, no millis
- `TypeNameHandling = Auto` — embed `$type` only for polymorphic graphs
  (irrelevant here; none of our payloads are polymorphic)

### Protected payload

The inner JSON is then wrapped in a "protected payload":

```
{
  "d":  <UTC datetime, ISO without tz>,
  "tp": "<time print, see §4.1>",
  "sp": "<serialized payload JSON string>"
}
```

### Encrypting & encoding

```
plaintext  = json(protected_payload)
ciphertext = AES-128-CBC + PKCS7(plaintext, key=PBKDF2-SHA1(passphrase, salt, 1000 iters, 16 bytes))
output     = salt || iv || ciphertext             // 16 + 16 + N bytes
encoded    = Base64Url( Base64( output ) )        // YES, doubly-base64-encoded
```

The .NET code does `Base64UrlEncoder.Encode(Encryption.Encrypt(...))`, and
`Encryption.Encrypt` already returns a base64 string, so the byte stream that
reaches `psp` is base64-url-of-base64-of-binary. We replicate this verbatim
because the server reverses the same chain.

### Signing

```
signature = Base64Url( SHA512_hex( encoded + real_key ) )
psp       = signature + "." + encoded
```

Note that `signature` uses the **real** key (PublicTransactionKey or
TerminalTransactionKey), not the buggy AES passphrase from §4.2.

### 4.1 Time print

`TransactionSecurityProvider.GetTimePrint` does:

```csharp
SHA512_hex( $"{time.SetUtc():<format>}" + key )
```

The custom DateTime format strings are:

- public:   `T'`'MM'yyyy'-'dd'HH':'mm':'ss`  (raw bytes: `T` + U+2019 + U+2018 + `MM` + …)
- terminal: `T'`'dd'HH''MM'yyyy'-:'mm':'ss` (similar mess)

The U+2019 (`’`) and U+2018 (`‘`) characters are **Unicode curly quotes**, NOT
the ASCII apostrophe `'` that C# uses as a custom-format literal-text
delimiter. So .NET treats them as literal output characters. Other unrecognized
chars (`T`, `-`) are also output literally. `MM yyyy dd HH mm ss` are real
custom-format specifiers; `:` is the time separator (= `:` in invariant
culture).

Our Python `dotnet_format()` (in the POC) re-implements just the specifiers we
need and treats everything else as literal — including `:` and `-` because in
the invariant culture they resolve to themselves.

### 4.2 The `string.Reverse().ToString()` developer bug

`TransactionSecurityProvider.EncodePayload`:

```csharp
return base.SecurityHelper.Encode(payload, key.Reverse().ToString() ?? string.Empty);
```

The intent was clearly "use the reversed key as the AES passphrase". But:

- `key.Reverse()` is the LINQ extension that returns `IEnumerable<char>`.
- `.ToString()` on a LINQ iterator falls through to the default `Object.ToString()`,
  which returns the iterator's full type-name string.
- In .NET 6+ that string is the literal:
  ```
  System.Linq.Enumerable+ReverseIterator`1[System.Char]
  ```

So the **AES passphrase is a constant** (the type name), regardless of which
key is being "reversed". Both client and server have the same bug, so they
interoperate. The signing step (§4) does use the real key, so the signature is
still a per-key MAC; only the encryption layer is effectively MAC-only-protected.

We hard-code this exact string in the POC as `DOTNET_BUG_KEY` and pass it to
PBKDF2.

> **Security note:** this means anyone with the decompiled source can decrypt
> every signed transaction posted to the auth API. The signature still prevents
> *tampering* (you need the real key to forge), but confidentiality of the inner
> payload is broken. See `docs/security-findings.md` for our broader RE notes.

---

## 5. Putting it together — the request flow

For each call:

```
1. inner_json   = newtonsoft_dumps(request_args)     // e.g. ApplicationLoginArgs
2. time         = UTC now, no millis
3. tp           = SHA512_hex( format(time, fmt) + real_key )
4. protected    = newtonsoft_dumps({d: time, tp: tp, sp: inner_json})
5. cipher_b64   = base64( salt(16) || iv(16) || AES-CBC-PKCS7(protected, PBKDF2(DOTNET_BUG_KEY, salt, 1000, 16)) )
6. encoded      = base64url_no_padding(cipher_b64)
7. signature    = base64url_no_padding(SHA512_hex(encoded + real_key))
8. envelope     = {tpv: 1, psp: f"{signature}.{encoded}"[, tid: <terminal_id>]}
9. POST envelope as application/json
```

Response is the same envelope shape with server-issued data — decode in reverse
order:

```
1. split psp at "."
2. base64url_decode the encoded part → base64 string
3. base64 decode → salt || iv || ciphertext
4. PBKDF2 + AES-CBC decrypt with DOTNET_BUG_KEY
5. parse JSON → {d, tp, sp}
6. parse sp as JSON → actual response body
```

---

## 6. Python implementation map

| .NET symbol | Python equivalent (POC) |
|---|---|
| `Encryption.Encrypt` (`IngeliStd/IngeliStd.Cryptography/Encryption.cs`) | `aes_encrypt()` |
| `Encryption.Decrypt` | `aes_decrypt()` |
| `SecurityHelper.ComputeSha256Hash` | `sha256_hex()` |
| `SecurityHelper.ComputeSha512Hash` | `sha512_hex()` |
| `Base64UrlEncoder.Encode` | `b64url()` |
| `TransactionSecurityProvider.GetTimePrint` | `dotnet_format()` + `sha512_hex()` |
| `TransactionSecurityProvider.EncodePayload` | hard-coded `DOTNET_BUG_KEY` |
| `TransactionSecurityProvider.SignPayload` | `sign_payload()` |
| `Generate{Public,Terminal}Transaction` | `make_public_signed()` / `make_terminal_signed()` |
| `TerminalApiRemoteClient.RegisterTerminal` | `register_terminal()` |
| `ApplicationRemoteClient.Login` | `login()` |

Total: ~280 lines of Python, only dependency beyond the stdlib is `pycryptodome`
(already in `polyconnect_bridge/requirements.txt`).

---

## 7. POC validation results

Running `scripts/login_poc/polyconnect_login.py` against the real production
endpoint with credentials from `.env`:

```
[1] POST /Irc/Terminal/RegisterTerminal
    → state=100 (Success), terminal_id=<24-char hex>, ttk=<86-char base64url>
[2] POST /Irc/Application/Login
    → state=0  (AuthenticationResult.Success), token=<516 chars>,
      url=https://polytropic.user-app.pool.mytech-connect.io/from-native
[3] GET <url>/<token>
    → HTTP 200, 20 053 bytes, body = `<!DOCTYPE html><html lang="en"><head>… Polytropic User App …`
```

The freshly-issued token loads the same Blazor SPA that the v1 mitm-captured
token does. The bridge's existing Playwright code works against it unchanged.

---

## 8. v2 integration plan

The bridge currently has three subsystems we replace:

| v1 | v2 |
|---|---|
| `polyconnect_bridge/capture_manager.py` — mitmproxy lifecycle | `polyconnect_bridge/auth.py` — `RegisterTerminal` + `Login`, persistent terminal creds |
| `polyconnect_bridge/setup_ui.py` (port 8080) — phone-facing CA / proxy wizard | Email/password form on the same port; one POST and we're done |
| `scripts/capture/` — JWT + IDs capture | Deleted (kept under `scripts/login_poc/` as reference) |

Persistent state in `/data/` becomes:

```
/data/
  ├── terminal.json          # { terminal_id, terminal_transaction_key } — long-lived
  ├── credentials.json       # { email, password_hash } — for silent re-auth
  ├── session.json           # { token, url, expires_at } — short-lived; auto-refreshed
  └── ids.json               # { installation_id, heat_pump_id } — discovered from SPA
```

Re-auth happens on any 401/403 from the Blazor app; we silently re-run `Login`
with the stored credentials (the terminal stays registered).

`installation_id` and `heat_pump_id` discovery moves into a "discover" routine
that runs once on first login: Playwright loads `<url>/<token>`, navigates
through the SPA, captures the IDs from the URL path. After that, the IDs stay
in `/data/ids.json` and we never need them re-discovered unless the user adds
a new heat pump.
