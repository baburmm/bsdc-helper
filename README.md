# BSDC Helper

A small, single-login file drop for moving files between your personal machine and
your work machine. Upload from one, sign in from the other, download, then delete.

- **One password.** No accounts, no user database. The password lives in a Container App secret.
- **Blob Storage backing.** Files survive restarts, scale-to-zero, and new revisions.
- **Admin can delete.** Every signed-in session is the admin — upload, download, delete.
- **Type + size limits.** `.zip`, `.pptx`, `.ppt`, `.xlsx`, `.xls`, `.xlsm`, `.csv` by default, 512 MB each.

## What's here

```
app/
  main.py       FastAPI routes: pages, auth API, file API
  auth.py       signed-cookie session, login throttle
  storage.py    Azure Blob backend + local-disk backend for dev
  config.py     all settings, read from env vars
  static/       login page, file browser, styles
Dockerfile      python:3.12-slim, runs as a non-root user
deploy.ps1      one-shot deploy to Azure Container Apps
tests/          end-to-end smoke test
```

## Deploy to Azure

Prerequisites: [Azure CLI](https://aka.ms/installazurecli), then `az login`.
`az containerapp up` builds the image remotely with ACR Tasks, so you don't need
Docker running locally.

```powershell
.\deploy.ps1 -ResourceGroup rg-bsdc-helper -Location eastus
```

It will prompt for the admin password, then create:

| Resource | Purpose |
|---|---|
| Storage account + `uploads` container | where the files actually live |
| Container Apps environment | the runtime |
| Container App `bsdc-helper` | the app, HTTPS ingress, scale 0→1 |
| Container Registry | holds the image `containerapp up` builds |

At the end it prints your URL — something like
`https://bsdc-helper.<region>.azurecontainerapps.io`. Open it on either machine,
enter the password, done.

Redeploy after a code change:

```powershell
az containerapp up --name bsdc-helper --resource-group rg-bsdc-helper --source .
```

### Doing it from the portal instead

If you'd rather click through it:

1. **Create a storage account** (Standard LRS). Under *Data storage → Containers*, add a
   container named `uploads`. Copy a connection string from *Security + networking →
   Access keys*.
2. **Create a Container App.** Choose *Use quickstart image* to get through the wizard,
   with ingress **enabled**, traffic **accepting from anywhere**, target port **8000**.
3. Build and push this image to a registry the app can read (ACR is easiest):
   `az acr build --registry <your-acr> --image bsdc-helper:v1 .`
4. In the Container App → *Containers → Edit and deploy*, point the image at
   `<your-acr>.azurecr.io/bsdc-helper:v1`.
5. In *Settings → Secrets*, add `admin-password`, `secret-key`, and `storage-conn`.
   Then in *Containers → Environment variables*, add the variables from the table below,
   referencing those secrets.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `ADMIN_PASSWORD` | *(none)* | **Required.** The single login. Nobody can sign in if unset. |
| `SECRET_KEY` | random | Signs session cookies. Set it, or everyone is signed out on each restart. |
| `AZURE_STORAGE_CONNECTION_STRING` | — | Use this *or* `AZURE_STORAGE_ACCOUNT`. |
| `AZURE_STORAGE_ACCOUNT` | — | For managed identity instead of a connection string (see below). |
| `BLOB_CONTAINER` | `uploads` | Blob container name. |
| `MAX_UPLOAD_MB` | `512` | Per-file limit. |
| `ALLOWED_EXTENSIONS` | `zip,pptx,ppt,xlsx,xls,xlsm,csv` | Comma-separated, no dots. |
| `SESSION_HOURS` | `12` | How long a sign-in lasts. |
| `COOKIE_SECURE` | `true` | Set `false` only for plain-http local testing. |

With neither storage variable set, the app falls back to a local `./_local_files`
folder. That's for development only — Container Apps replicas have ephemeral disks,
so anything written there disappears when the app scales to zero.

### Managed identity instead of a connection string

Cleaner, since no key is stored anywhere:

```powershell
az containerapp identity assign --name bsdc-helper --resource-group rg-bsdc-helper --system-assigned
$principal = az containerapp identity show --name bsdc-helper --resource-group rg-bsdc-helper --query principalId -o tsv
$scope = az storage account show --name <storage-account> --resource-group rg-bsdc-helper --query id -o tsv
az role assignment create --assignee $principal --role "Storage Blob Data Contributor" --scope $scope

az containerapp update --name bsdc-helper --resource-group rg-bsdc-helper `
  --set-env-vars "AZURE_STORAGE_ACCOUNT=<storage-account>" `
  --remove-env-vars AZURE_STORAGE_CONNECTION_STRING
```

## Running locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env    # then set ADMIN_PASSWORD and COOKIE_SECURE=false
.\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Then <http://localhost:8000>. Files land in `./_local_files`.

Run the smoke test (24 checks: auth, upload, type/size limits, path traversal,
download, delete):

```powershell
.\.venv\Scripts\python tests\smoke_test.py
```

## Security notes

Worth knowing, since this is on the public internet:

- The URL is reachable by anyone; the password is the only thing in front of it. Use a
  long one. Sign-ins are throttled to 8 attempts per 5 minutes per IP, per replica.
- Session cookies are `HttpOnly`, `Secure`, `SameSite=Lax`, signed with `SECRET_KEY`.
- Filenames are stripped of paths and unusual characters before they touch storage, so
  `../../etc/passwd` is reduced to `passwd`. Collisions get `-1`, `-2` suffixes rather
  than overwriting.
- Downloads always go out as `Content-Disposition: attachment`, so nothing uploaded
  can be rendered as a page on the app's own origin.
- Uploads are buffered in the container before being written to Blob Storage, so a
  512 MB upload needs 512 MB of ephemeral disk. Keep the app's memory/CPU profile in
  mind if you raise the limit a lot. If very large uploads stall at the ingress rather
  than in the app, split the zip or lower `MAX_UPLOAD_MB`.
- Blob public access is disabled on the storage account — files are only reachable
  through the app.

If you want to tighten it further, put the Container App behind Entra ID
(*Settings → Authentication → Add identity provider → Microsoft*) and restrict it to
your own tenant accounts. The password login still works underneath.

## Cost

Scale-to-zero is on (`--min-replicas 0`), so with light use this sits in the Container
Apps free grant, and storage is a few cents per GB-month. The app cold-starts in a few
seconds after idling.
