# Exchange Online alias management

Synchronous Python service that wraps a PowerShell Core script
(`scripts/exchange/manage_alias.ps1`) to add, remove and list SMTP proxy
addresses (aliases) on a shared mailbox in Exchange Online.

Authentication uses an Azure AD application (service principal) with a
certificate — fully non-interactive, suitable for cron / Celery / Django
request paths.

## Why PowerShell?

Microsoft Graph API does **not** allow updating `proxyAddresses` on a mailbox.
The historical `Set-Mailbox -EmailAddresses` cmdlet remains the only
supported way. There is no REST equivalent. We invoke `pwsh` with the
`ExchangeOnlineManagement` module and parse the JSON output.

## Architecture

```
Django (Application.save / delete)
      │
      ▼
exchange.integration.{provision,deprovision}_alias_for_application
      │
      ▼
exchange.services.ExchangeAliasService.{add,remove,list}_alias
      │
      ▼  subprocess.run([pwsh, -File, manage_alias.ps1, -Action, ...])
      │
      ▼
scripts/exchange/manage_alias.ps1
      │  Connect-ExchangeOnline (cert auth)
      │  Set-Mailbox / Get-Mailbox
      ▼
Exchange Online
```

The service:

- Validates inputs in Python (regex + forbidden chars) **before** invoking pwsh.
- Calls `pwsh` with `shell=False` and a list of args (no string concatenation).
- Forwards secrets via the subprocess **environment**, never on the command line.
- Times out after `EXCHANGE_PS_TIMEOUT` seconds (default 60).
- Maps the script's `error_code` field to typed Python exceptions:

| Script `error_code`      | Python exception      |
|--------------------------|-----------------------|
| `missing_param`          | `InvalidAliasInput`   |
| `missing_env`            | `ExchangeConfigError` |
| `auth_failed`            | `ExchangeAuthError`   |
| `mailbox_not_found`      | `MailboxNotFound`     |
| `alias_already_exists`   | `AliasAlreadyExists`  |
| `alias_not_found`        | `AliasNotFound`       |
| (timeout, internal)      | `ExchangeTimeoutError`, `ExchangeError` |

## Settings

All consumed from `django.conf.settings` (see `config/settings/base.py`):

| Setting                    | Required | Description                                        |
|----------------------------|----------|----------------------------------------------------|
| `EXCHANGE_PS_SCRIPT_PATH`  | yes      | Absolute path to `manage_alias.ps1`.               |
| `EXCHANGE_APP_ID`          | yes      | Azure AD app (client) ID.                          |
| `EXCHANGE_TENANT`          | yes      | e.g. `contoso.onmicrosoft.com`.                    |
| `EXCHANGE_CERT_THUMBPRINT` | one of   | Thumbprint of cert in `Cert:\CurrentUser\My`. **Windows only** — leave empty on Linux. |
| `EXCHANGE_CERT_FILE_PATH`  | one of   | Path to PFX / PEM file. **Required on Linux.** If both vars are set, the script picks the thumbprint, which fails on Linux. |
| `EXCHANGE_CERT_PASSWORD`   | optional | PFX password (only with `EXCHANGE_CERT_FILE_PATH`).|
| `EXCHANGE_SHARED_MAILBOX`  | yes      | Primary SMTP address of the shared mailbox.        |
| `EXCHANGE_PS_TIMEOUT`      | optional | Subprocess timeout in seconds (default 60).        |

## Programmatic usage

```python
from exchange import ExchangeAliasService, AliasAlreadyExists, MailboxNotFound

service = ExchangeAliasService()

try:
    service.add_alias(mailbox="shared@contoso.com", alias="newapp@contoso.com")
except AliasAlreadyExists:
    ...
except MailboxNotFound:
    ...

aliases = service.list_aliases("shared@contoso.com")
service.remove_alias(mailbox="shared@contoso.com", alias="newapp@contoso.com")
```

The Application model wires this in automatically via
`exchange.integration.provision_alias_for_application` / `deprovision_alias_for_application`.
Failures are logged at ERROR level but do not block save/delete.

> ⚠️ The `EXCHANGE_*` env vars must be present in the `DOTENV_PROD` GitHub
> Secret, not only in the local `.env` on the server. The CI/CD deploy
> overwrites `.env` from that secret on every push to `main`, so editing
> the server-side `.env` directly is only valid until the next deploy.
> See **Adding a new environment variable** in `CLAUDE.md`.

## Deployment — server side

### 1. Install PowerShell Core (Debian/Ubuntu)

Microsoft's official repository:

```bash
# Ubuntu 22.04 / 24.04 — adjust if needed (see https://learn.microsoft.com/powershell/scripting/install/install-ubuntu)
sudo apt-get update
sudo apt-get install -y wget apt-transport-https software-properties-common

source /etc/os-release
wget -q "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb"
sudo dpkg -i packages-microsoft-prod.deb
rm packages-microsoft-prod.deb

sudo apt-get update
sudo apt-get install -y powershell

pwsh --version    # PowerShell 7.x
```

### 2. Install the ExchangeOnlineManagement module

System-wide so the `django` service user can also load it:

```bash
sudo pwsh -Command "Install-Module -Name ExchangeOnlineManagement -Force -Scope AllUsers -AllowClobber"
sudo pwsh -Command "Get-Module -ListAvailable ExchangeOnlineManagement | Select-Object Name, Version"
```

### 3. Generate a self-signed certificate (for tests)

For production prefer a cert from your CA. For dev/staging:

```bash
# Pick a strong passphrase if you want; here we use empty for simplicity.
openssl req -x509 -newkey rsa:4096 -sha256 -days 730 \
  -nodes \
  -subj "/CN=PushIT-Exchange" \
  -keyout pushit-exchange.key \
  -out pushit-exchange.crt

# Bundle private key + cert into a PFX for PowerShell consumption
openssl pkcs12 -export \
  -inkey pushit-exchange.key \
  -in pushit-exchange.crt \
  -out pushit-exchange.pfx \
  -password pass:CHANGE_ME

# Compute the SHA-1 thumbprint that PowerShell uses
openssl x509 -in pushit-exchange.crt -fingerprint -noout | tr -d ':' | awk -F= '{print $2}'
```

The thumbprint is informative on Linux (you don't import the cert anywhere
locally — Azure validates it against the public key uploaded in step B).
On Windows it goes into `EXCHANGE_CERT_THUMBPRINT` after the import in
step 5 Option A.

### 4. Upload the public key (.crt) to Azure AD

In your Azure AD app registration → **Certificates & secrets** →
**Certificates** → **Upload certificate**. Upload `pushit-exchange.crt`
(public key only — never upload the .pfx or .key).

### 5. Deploy the certificate on the server

The `django` service user must be able to read the cert.

> ⚠️ **`Import-PfxCertificate` and `Connect-ExchangeOnline -CertificateThumbprint`
> are Windows-only.** PowerShell Core on Linux does not ship the `PKI` module
> and the cert-store flow simply does not exist (`Connect-ExchangeOnline` on
> Linux does not even expose `-CertificateThumbprint`). On Linux you **must**
> use Option B (file-based).

**Option B — file-based (Linux, recommended):**

```bash
sudo mkdir -p /etc/pushit
sudo mv /tmp/pushit-exchange.pfx /etc/pushit/
sudo chown django:django /etc/pushit/pushit-exchange.pfx
sudo chmod 600 /etc/pushit/pushit-exchange.pfx
```

Then in `.env`:

```env
EXCHANGE_CERT_THUMBPRINT=                                  # leave empty on Linux
EXCHANGE_CERT_FILE_PATH=/etc/pushit/pushit-exchange.pfx
EXCHANGE_CERT_PASSWORD=<password used in `openssl pkcs12 -export`>
```

> ⚠️ If both `EXCHANGE_CERT_THUMBPRINT` and `EXCHANGE_CERT_FILE_PATH` are set,
> `manage_alias.ps1` gives priority to the thumbprint and the connection
> fails on Linux with *"A parameter cannot be found that matches parameter
> name 'CertificateThumbprint'."* Always clear the thumbprint when using
> file mode.

The `django` service user also needs a real home directory — the
`ExchangeOnlineManagement` module reads `$HOME` when loading and fails with
*"Cannot bind argument to parameter 'Path' because it is an empty string"*
otherwise:

```bash
getent passwd django                  # confirm home is /home/django (or similar)
sudo mkdir -p /home/django/.local/share/powershell
sudo chown -R django:django /home/django
sudo chmod 750 /home/django
```

This only matters for the manual smoke test under `sudo`; under systemd
(Gunicorn / Celery) `HOME` is set automatically from `/etc/passwd`.

**Option A — thumbprint store (Windows only):**

If you run this on a Windows host, you can import the PFX into the user
cert store and use `EXCHANGE_CERT_THUMBPRINT` instead of the file path:

```powershell
$pfx = "C:\path\to\pushit-exchange.pfx"
$pwd = ConvertTo-SecureString -String "CHANGE_ME" -AsPlainText -Force
Import-PfxCertificate -FilePath $pfx -CertStoreLocation Cert:\CurrentUser\My -Password $pwd |
  Select-Object Thumbprint
```

## Azure / Entra ID — what you need to do

This is the part that is **not** scripted and must be done in the Entra portal.

### A. Register the application

1. Sign in to <https://entra.microsoft.com>.
2. **Applications → App registrations → New registration**.
   - Name: `PushIT Exchange Alias Manager` (or anything you like).
   - Supported account types: **Accounts in this organizational directory only**.
   - Redirect URI: leave empty (this is daemon-only).
3. After creation, copy:
   - **Application (client) ID** → goes into `EXCHANGE_APP_ID`.
   - **Directory (tenant) ID** is informative; `EXCHANGE_TENANT` must be the
     tenant **domain** (e.g. `foxugly.onmicrosoft.com`), **not** the GUID.
     Putting the GUID there returns *"Organization cannot be a Guid, please
     enter the name of the tenant instead."*

### B. Upload the certificate

1. In your new app registration → **Certificates & secrets** → **Certificates**
   → **Upload certificate**.
2. Upload the `.crt` (or `.cer`) generated in step 3 above. Never upload the
   `.pfx` or `.key`.
3. Confirm the displayed thumbprint matches the one you computed locally.

### C. Grant the Exchange.ManageAsApp permission

1. In your app → **API permissions** → **Add a permission** → **APIs my
   organization uses** → search for **Office 365 Exchange Online** →
   **Application permissions** → check **Exchange.ManageAsApp**.
2. Click **Add permissions**.
3. Click **Grant admin consent for {tenant}** (you must be a Global Admin or
   Privileged Role Admin).

### D. Register the service principal in Exchange Online and assign a role

> The Azure AD service principal you just created is **not** automatically
> visible to Exchange. Exchange maintains its own `ServicePrincipal` table
> and you have to register the app there before you can assign it a role.
> The Exchange Admin Center web UI handles this poorly — service principals
> often don't appear in role-group "Add member" search at all. Use the
> PowerShell flow below; it is the supported pattern.

**1. Find the Object ID of the enterprise application.**

In <https://entra.microsoft.com> → **Enterprise applications** (NOT
"App registrations") → click your app → copy the **Object ID** shown on
the Overview page. This is different from both the Application (client) ID
and from the App registration's Object ID.

**2. Connect to Exchange Online with an admin account.**

From a Windows machine with `ExchangeOnlineManagement` installed (or from
the server in interactive `pwsh`):

```powershell
Connect-ExchangeOnline -UserPrincipalName admin@yourtenant.onmicrosoft.com
```

> On Linux this triggers a device-code flow — open the printed URL on a
> machine with a browser and enter the displayed code.

**3. Check whether an Exchange `ServicePrincipal` already exists for the app:**

```powershell
Get-ServicePrincipal | Where-Object { $_.AppId -eq "<your AppId>" }
```

**4. If nothing comes back, register it (Exchange-side only — does not
re-create anything in Azure AD):**

```powershell
New-ServicePrincipal `
  -AppId "<your AppId>" `
  -ObjectId "<Object ID from Enterprise applications>" `
  -DisplayName "<friendly name>"
```

**5. Assign the role directly to the AppId.**

The least-privileged role for `Set-Mailbox -EmailAddresses` (which is what
`manage_alias.ps1` invokes) is **Mail Recipients**:

```powershell
New-ManagementRoleAssignment `
  -App "<your AppId>" `
  -Role "Mail Recipients"
```

> Use the individual role `Mail Recipients`, **not** the role *group*
> `Recipient Management`. Adding a service principal to a role group via
> the EAC UI is unreliable; direct role assignment via `-App` is the
> documented pattern for app-only auth.

**6. Verify:**

```powershell
Get-ManagementRoleAssignment -RoleAssignee "<your AppId>"
```

You should see one row with `Role = Mail Recipients`,
`RoleAssigneeType = ServicePrincipal`, `AssignmentMethod = Direct`.

**7. Propagation.**

Allow **5–15 minutes** before authenticated calls work. Until propagation
completes, `Connect-ExchangeOnline` returns *"The role assigned to
application X isn't supported in this scenario."* — that error means the
role is not yet visible to the EXO backend, not that the assignment is
wrong.

> Reference: <https://learn.microsoft.com/powershell/exchange/app-only-auth-powershell-v2>

### E. Configure the shared mailbox

The mailbox you target with `EXCHANGE_SHARED_MAILBOX` must be a real Exchange
mailbox (shared or licensed). Verify with:

```bash
pwsh -File scripts/exchange/manage_alias.ps1 -Action list -Mailbox shared@yourtenant.com
```

If the JSON response contains `"success": true` and a list of addresses, you
are good to go.

## Manual integration test

The script reads its parameters from environment variables (not via Django's
`.env` loader). To smoke-test from a shell, source `.env` first and use
`sudo -H` so `HOME` is set correctly for the `django` service user:

```bash
# Smoke test — list addresses
sudo -H -u django bash -c '
  set -a; source /var/www/django_websites/PushIT_server/.env; set +a
  pwsh -File /var/www/django_websites/PushIT_server/scripts/exchange/manage_alias.ps1 \
    -Action list -Mailbox "$EXCHANGE_SHARED_MAILBOX"
'

# Add an alias
sudo -H -u django bash -c '
  set -a; source /var/www/django_websites/PushIT_server/.env; set +a
  pwsh -File /var/www/django_websites/PushIT_server/scripts/exchange/manage_alias.ps1 \
    -Action add -Mailbox "$EXCHANGE_SHARED_MAILBOX" -Alias smoketest@yourtenant.com
'

# Remove it
sudo -H -u django bash -c '
  set -a; source /var/www/django_websites/PushIT_server/.env; set +a
  pwsh -File /var/www/django_websites/PushIT_server/scripts/exchange/manage_alias.ps1 \
    -Action remove -Mailbox "$EXCHANGE_SHARED_MAILBOX" -Alias smoketest@yourtenant.com
'
```

A successful `list` returns:

```json
{"success":true,"data":["SMTP:hello@yourtenant.com","smtp:..."]}
```

> `sudo -H` is required: the `ExchangeOnlineManagement` module reads `$HOME`
> when loading. Plain `sudo -u django` keeps root's `HOME` and the module
> fails to load. `set -a; source .env; set +a` exports every var so they
> are inherited by the `pwsh` subprocess.

In Django runtime (Gunicorn / Celery) none of this dance is needed —
`exchange.services.ExchangeAliasService` injects the `EXCHANGE_*` env vars
into the subprocess explicitly via `subprocess.run(env=...)`, and systemd
already sets `HOME` from `/etc/passwd`.

From Django:

```bash
python manage.py shell -c "
from exchange import ExchangeAliasService
from django.conf import settings
print(ExchangeAliasService().list_aliases(settings.EXCHANGE_SHARED_MAILBOX))
"
```

## Troubleshooting

- **`auth_failed`: "A parameter cannot be found that matches parameter name 'CertificateThumbprint'"**
  → On Linux pwsh, `Connect-ExchangeOnline` does not expose
  `-CertificateThumbprint`. Clear `EXCHANGE_CERT_THUMBPRINT` in `.env` and
  rely on `EXCHANGE_CERT_FILE_PATH` + `EXCHANGE_CERT_PASSWORD` (Option B).
- **`auth_failed`: "Organization cannot be a Guid, please enter the name of the tenant instead"**
  → `EXCHANGE_TENANT` must be the tenant **domain** (e.g. `foxugly.onmicrosoft.com`),
  not the Directory ID GUID.
- **`auth_failed`: "The role assigned to application X isn't supported in this scenario"**
  → The Exchange RBAC role is not (yet) assigned to the service principal,
  or has not propagated. Re-check section D and wait 5–15 min after
  `New-ManagementRoleAssignment`.
- **Module load error: "Cannot bind argument to parameter 'Path' because it is an empty string"**
  → `$HOME` is empty or points to a non-existent directory for the user
  running pwsh. Use `sudo -H -u django` (not bare `sudo -u django`) and
  ensure `/home/django` exists with the right ownership (see step 5).
- **`auth_failed`: other** — cert not yet uploaded in Azure, admin consent
  not granted, PFX password wrong, or wrong AppId / tenant. Run
  `pwsh -NoProfile -Command "Import-Module ExchangeOnlineManagement -Verbose"`
  to isolate the load step from the connect step.
- **`missing_env`** — the `EXCHANGE_*` env vars were not exported into the
  pwsh subprocess. Source `.env` with `set -a; source .env; set +a` before
  invoking pwsh, or rely on the Django runtime which injects them
  explicitly via `subprocess.run(env=...)`.
- **`mailbox_not_found`** — verify the SMTP address is the *primary* one and
  the mailbox is actually licensed in Exchange Online.
- **`alias_already_exists`** — by design; treat as idempotent success at the
  caller level if you want.
- **Empty stdout / non-zero exit** — run the script manually with
  `-Verbose` to see the full PowerShell error.

## Performance

V1 is intentionally simple: each call spawns a fresh `pwsh` and incurs
~10s for `Connect-ExchangeOnline`. Acceptable for the current volume
(application creation/deletion, not on the hot path). If we ever need
sub-second latency, options are:

- A persistent `pwsh` worker process holding the session open.
- Batched operations via Celery.

Both are out of scope for V1.
