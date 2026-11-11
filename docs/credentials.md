# Credentials

Bring your own key. A credential is a logical name, a provider and one secret. The spec
names the credential; a candidate or judge receives a resolved handle on `RunContext`
and never a settings object. The secret lives in the encrypted vault, not in the YAML.

The same vault is managed from a browser at [console.md#credentials](console.md#credentials):
add, test, disable, enable and delete are ordinary form posts backed by the JSON API,
and every page shows metadata and a fingerprint prefix rather than a secret.

## Master key

The vault encrypts with Fernet (AES-128-CBC plus HMAC-SHA256). The master key is 32
bytes, written as 44 urlsafe base64 characters.

Resolution order at process start:

1. `EVALH_MASTER_KEY` when it is set and non-empty.
2. `EVALH_MASTER_KEY_FILE` when it is set: the file is read as UTF-8 and stripped, then
   decoded exactly like the environment value.
3. Neither is available: the harness generates a key, prints it exactly once on stderr
   with `EVALH_MASTER_KEY=<key>` and an instruction to store it, and exits 2. Nothing is
   written to disk. Re-run the command with the key in the environment.

A malformed value exits 2 with a spec error that names the variable and the expected
shape. A malformed value never prints a generated key.

```text
spec error: EVALH_MASTER_KEY='<redacted>': the master key is not valid urlsafe base64;
expected 44 urlsafe base64 characters that decode to 32 bytes. Fix: set EVALH_MASTER_KEY
to an unmodified key printed by the first run
```

Commands that open the vault (`run`, `serve` and every `credentials` subcommand)
require a key. `run --dry-run` and `validate` do not: they read credential metadata
only, and they do not create the database.

Generate a key by hand when provisioning a container:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the key out of the repository and out of the container image. Losing it means
re-entering keys; there is no recovery path, which is the correct failure mode.

## Command line

```bash
# Read the secret from stdin, or from an environment variable, or prompt without echo.
evalh credentials add openai-prod --provider openai --secret-stdin
evalh credentials add openai-prod --provider openai --secret-env OPENAI_API_KEY
evalh credentials add openai-prod --provider openai

evalh credentials list
evalh credentials test openai-prod
evalh credentials test openai-prod --verify-url https://api.openai.com/v1/models
evalh credentials delete openai-prod
evalh credentials delete openai-prod --yes
```

`add` encrypts before it writes. A duplicate name is a spec error (exit 2). `delete`
asks for confirmation unless `--yes` is passed. `list` prints name, provider, creation
time, last successful verification and last error, and never the secret.

```text
name                 provider       created                last_verified          last_error
openai-prod          openai         2026-10-25T15:20:03-04:00  2026-10-25T15:21:11-04:00  -
```

`test` makes one provider request. A rejected key prints the provider's message and exits
1; a successful request exits 0. Unknown names and a missing verification URL exit 2.

## What is stored

The `credentials` table holds only ciphertext and metadata:

| Column | Meaning |
| --- | --- |
| `name` | Logical credential name used by the spec. Primary key. |
| `provider` | Auth shape selector, for example `openai`, `anthropic`, `azure-openai`, `opencode`, `none`. |
| `ciphertext` | Fernet token. The secret exists nowhere else in the database. |
| `created_at` | When the credential was added. |
| `last_ok_at` | The last successful verification, or NULL. |
| `last_error` | The last provider message, redacted, or NULL. |
| `disabled` | False by default. A disabled credential is never used by the harness. |

Specs, run snapshots, prices, artifacts and events never contain the secret. A database
dump, including the raw SQLite file, contains ciphertext only; the test suite asserts
this with a canary value. The schema version is 5: version 5 adds `disabled` with a
false default, so a row stored by issue #6 reads back as enabled.

## Verification request

`test` performs exactly one `GET` against the base URL:

- The URL comes from `EVALH_VERIFY_URL`, or `--verify-url` for one call.
- The timeout is 5 seconds and there are no retries. A transport failure is recorded as
  `last_error` and exits 1.
- Auth shape is chosen by provider. Bearer is the default:

| Provider | Header |
| --- | --- |
| default, `openai`, `openai-compatible`, `opencode`, `custom` | `Authorization: Bearer <secret>` |
| `anthropic` | `x-api-key: <secret>` |
| `azure-openai` | `api-key: <secret>` |
| `none` | no auth header |

- A 2xx response is accepted. The message is the provider's JSON `error` string, the
  JSON `error.message`, the JSON `message`, the plain body, or the HTTP reason phrase,
  in that order.
- A non-2xx response is rejected. The verifier returns the provider's message verbatim in
  `VerificationResult(ok=False, message=...)`.
- On success `last_ok_at` is stamped and `last_error` is cleared. On failure
  `last_error` is stamped and the last successful time is kept. The stored message
  is bounded to 500 characters, so a provider that answers with a whole HTML error
  page cannot flood the database or the console row.
- The CLI redacts the message before it prints or stores it, so a provider that echoes
  the key in its error text does not leak it. The console's Test button takes the same
  one-request path through `credential_service.verify_credential` and stores the same
  redacted row; it restores the message from `last_error` rather than from the redirect.

### Contract for a second verifier

```python
class CredentialVerifier(Protocol):
    async def verify(self, provider: str, secret: str) -> VerificationResult: ...
```

`VerificationResult` has `ok: bool` and `message: str`. A verifier must make at most one
provider request, must not retry, must not read global configuration (the base URL and
timeout arrive through factory options), and must return a rejection message without
rewriting it. Issue #17 extends this protocol rather than bypassing it.

The first-party implementation is `adapters.verify.http_verifier:build` with options
`base_url` and `timeout_s`.

## Redaction

At process start the CLI decrypts every stored secret and builds one `SecretRedactor`.
Every string that can surface passes through it:

- the logging handler configured for the `evalharness` logger (a `logging.Filter`),
- the event emitter's payloads,
- candidate answers, output and artifact metadata written to cell records,
- metric reason and evidence before checks are stored or sunk,
- artifact files, because the sink receives already-redacted records,
- the provider message printed and stored by `credentials test`.

A known secret is replaced with `[redacted:<credential-name>]`, longest secret first.
The console reuses the same redactor for a provider message before it stores or renders
it, and never puts a secret in an error value, a redirect or a log line.

The canary test
`tests/test_credentials_cli.py::test_vault_secret_is_redacted_from_logs_events_artifacts_and_cells`
stores a canary through the vault, runs a candidate that logs it, emits it in an event and
returns it as its answer, then asserts the canary is absent from the CLI output, the log
lines, the database dump, every event payload, both artifact files, the cell answer and
output, and the stored checks. `tests/test_cli_run.py` repeats the assertion for a
credential declared in the spec.
`tests/test_api_credentials.py::test_the_canary_never_surfaces_anywhere` covers the
browser seam: it submits a canary through the form and the JSON API, makes a stub
provider echo it in a rejection, and asserts it is absent from every HTML and JSON
body, every redirect, the captured log output, the database file and the stored row.

## Enable and disable

`disabled` is the one console-only switch: the command line has no
`credentials enable` or `credentials disable` subcommand, and the repository
method behind the routes is `set_credential_disabled`. A disabled credential
cannot be selected in the spec editor and is refused by the shared validator
in both the console and the CLI, so a spec cannot smuggle one in. Enabling it
again restores whatever state its test history describes: `verified` after a
passing test, `unverified` before one.

## Credentials for an HTTP candidate

The HTTP candidate uses the same vault. A spec names a credential, and the
adapter sends the resolved secret from `RunContext.credential` as
`Authorization: Bearer <secret>` by default, or in a named header with
`auth: header`. An endpoint that holds its own key uses `auth: none`, which
sends no header and needs no credential. The harness-owned default and the
missing-credential validation error are specified in
[http-candidate.md](http-candidate.md); this file remains the reference for the
vault itself.

## Credentials for a provider adapter

The two provider families use the same vault and the same resolution rule, for
candidates and for judges:

| Adapter family | `auth` default | Header sent | Credential arrives as |
| --- | --- | --- | --- |
| OpenAI-compatible | `bearer` | `Authorization: Bearer <secret>` | `RunContext.credential` for a candidate, `JudgeRequest.credential` for a judge |
| Anthropic-style | `header` | `x-api-key: <secret>` plus `anthropic-version` | same |

`auth: none` sends no auth header and needs no stored secret, which is how the
provider demo and the conformance suite run without a paid key. A missing
credential for a judge is a spec error naming `judges[i].credential`, before any
cell; the provider judge does not fail at request time. The full matrix is in
[providers.md](providers.md).

## Credentials for a repository source

A repository declared with an https `url` authenticates with its own
`credential`, or with `source.credential` when the repository names none. The
name must be declared under `credentials:` or stored in the vault, and it must
be usable; a disabled or unverified credential fails validation the same way a
candidate credential does. A repository with a local root rejects its own
`credential` field, and `source.credential` without at least one remote
repository is a spec error naming `source.credential`. The secret is passed to
git as an HTTP authorization header in the process environment and redacted from
a fetch failure.

## Rotation

Provider keys: a duplicate name is rejected, so rotate with `delete` then `add`, or add a
new name and update the spec. Old runs keep their spec snapshot and are unaffected.

Master key: v1 does not re-wrap stored ciphertext. Changing `EVALH_MASTER_KEY` makes every
existing credential unreadable; `run` and `credentials test` fail with an actionable spec
error naming the credential and the current-key fix. Recovery is `credentials delete`
followed by `credentials add` for each key, under the new master key. No automatic
rotation is attempted because a silent partial decrypt would be worse than a refusal.

## Limits

One container, one human: there are no accounts, no owner column and no tenancy. The
master key is visible to the process environment and to anyone who can read the key file;
a database dump alone is not enough to recover a secret. KMS-backed keys are out of scope
for v1. Artifacts that a candidate writes outside the harness artifact directory are the
candidate's responsibility.
