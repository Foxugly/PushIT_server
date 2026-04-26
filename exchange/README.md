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
| `EXCHANGE_CERT_THUMBPRINT` | one of   | Thumbprint of cert in `Cert:\CurrentUser\My`.      |
| `EXCHANGE_CERT_FILE_PATH`  | one of   | Path to PFX / PEM file (alternative to thumbprint).|
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

The thumbprint goes into `EXCHANGE_CERT_THUMBPRINT` once the cert is imported
(see step 5).

### 4. Upload the public key (.crt) to Azure AD

In your Azure AD app registration → **Certificates & secrets** →
**Certificates** → **Upload certificate**. Upload `pushit-exchange.crt`
(public key only — never upload the .pfx or .key).

### 5. Import the certificate into PowerShell on the server

The `django` service user must be able to read the cert.

**Option A — thumbprint store (preferred):**

```bash
sudo pwsh -Command \
  '$pfx = "/etc/pushit/pushit-exchange.pfx"; \
   $pwd = ConvertTo-SecureString -String "CHANGE_ME" -AsPlainText -Force; \
   Import-PfxCertificate -FilePath $pfx -CertStoreLocation Cert:\CurrentUser\My -Password $pwd | \
     Select-Object Thumbprint'
```

Take the printed thumbprint and set `EXCHANGE_CERT_THUMBPRINT` in `.env`.

> Note: PowerShell on Linux uses a per-user store rooted at `~/.dotnet/corefx/cryptography/x509stores/`.
> Run the import as the same user that runs the Django/Celery process (e.g. `sudo -u django pwsh ...`).

**Option B — file-based:**

Skip the import and set `EXCHANGE_CERT_FILE_PATH=/etc/pushit/pushit-exchange.pfx`
plus `EXCHANGE_CERT_PASSWORD=CHANGE_ME` directly. Make sure the file is owned
by `django` and `chmod 600`.

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
   - **Directory (tenant) ID** is informative; we use the tenant **domain**
     (e.g. `foxugly.onmicrosoft.com`) for `EXCHANGE_TENANT`.

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

### D. Assign an Exchange RBAC role

Application permissions alone are not enough — the service principal needs an
Exchange role. The least-privileged role for alias management is
**Recipient Management** (or **Organization Management** if you want full
control).

1. Open **Exchange admin center** → <https://admin.exchange.microsoft.com>.
2. Go to **Roles → Admin roles**.
3. Open the **Recipient Management** role group → **Members** tab → **Add**.
4. Search for the **service principal name** of your app (it appears under
   that name, not the app registration's display name) and add it.
5. Save. Propagation can take up to ~30 min.

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

Once the steps above are done:

```bash
# Smoke test — list addresses
pwsh -File scripts/exchange/manage_alias.ps1 -Action list -Mailbox $EXCHANGE_SHARED_MAILBOX

# Add an alias
pwsh -File scripts/exchange/manage_alias.ps1 \
  -Action add \
  -Mailbox $EXCHANGE_SHARED_MAILBOX \
  -Alias smoketest@yourtenant.com

# Remove it
pwsh -File scripts/exchange/manage_alias.ps1 \
  -Action remove \
  -Mailbox $EXCHANGE_SHARED_MAILBOX \
  -Alias smoketest@yourtenant.com
```

From Django:

```bash
python manage.py shell -c "
from exchange import ExchangeAliasService
from django.conf import settings
print(ExchangeAliasService().list_aliases(settings.EXCHANGE_SHARED_MAILBOX))
"
```

## Troubleshooting

- **`auth_failed`** — usually one of: cert thumbprint typo, cert not yet
  uploaded in Azure, admin consent not granted, role group membership not
  propagated yet. Wait 15-30 min after granting and retry.
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
