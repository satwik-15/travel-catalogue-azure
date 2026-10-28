# Travel Catalogue — Azure Cloud Computing Assignment

## What is implemented
- Flask web app: add destinations, list them, filter by country/category/maximum budget, validate inputs, and expose `/health`.
- Azure Blob Storage is used for persistent JSON data when `AZURE_STORAGE_CONNECTION_STRING` is configured.
- **Concurrency control:** an add operation downloads the current blob and its ETag, then writes with `IfNotModified`. If another request updates the blob first, the stale write is rejected and retried against the newest version (up to five attempts). This prevents the lost-update problem caused by unconditional overwrites.
- **Cloud feature 1 — CI/CD:** GitHub Actions installs dependencies and deploys on pushes to `main` (and can be run manually).
- **Cloud feature 2 — monitoring integration:** optional Azure Monitor OpenTelemetry / Application Insights instrumentation is enabled by `APPLICATIONINSIGHTS_CONNECTION_STRING`.

## Azure App Service configuration
Keep these settings under App Service → Settings → Environment variables (or Configuration):
- `AZURE_STORAGE_CONNECTION_STRING` = storage account connection string (secret)
- `AZURE_STORAGE_CONTAINER` = `travel-data`
- `AZURE_STORAGE_BLOB` = `destinations.json`
- `APPLICATIONINSIGHTS_CONNECTION_STRING` = connection string copied from the Application Insights resource
- `FLASK_SECRET_KEY` = a long random secret

Do not commit connection strings or publish profiles to GitHub. The deployment workflow expects the GitHub Actions secret `AZURE_WEBAPP_PUBLISH_PROFILE`.

## Finish the monitoring feature in Azure Portal (required one-time configuration)
1. Open/create an Application Insights resource in the same subscription/resource group as the Web App.
2. Copy its connection string into the Web App setting `APPLICATIONINSIGHTS_CONNECTION_STRING` and save/restart.
3. Push this code to `main` so GitHub Actions deploys it.
4. Visit `/` and `/health`, then open Application Insights → Live Metrics or Logs and verify requests/telemetry arrive. If telemetry does not arrive, inspect App Service logs and verify the connection string.
5. In Azure Monitor → Alerts, create an alert rule for the Web App using a supported metric such as HTTP 5xx responses or HTTP response time, set a threshold and evaluation window, and choose an action group/notification. Test it if your subscription allows.
6. Capture screenshots of the Application Insights resource/telemetry and the alert rule for the report. Do not claim an alert is active until the portal shows it as enabled.

## Deploy
The workflow in `.github/workflows/azure-deploy.yml` deploys automatically on a push to `main`. Before pushing, confirm the app name and GitHub secret match your Azure Web App. Python 3.11 is used in CI.

## Local run
```bash
python -m pip install -r requirements.txt
python app.py
```
Open `http://127.0.0.1:5000`. Without Azure environment variables, the app uses a local `data.json` file; the ETag concurrency protection applies to Azure Blob deployments.
