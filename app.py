
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core import MatchConditions
from azure.core.exceptions import (
    ResourceExistsError,
    ResourceModifiedError,
    ResourceNotFoundError,
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("travel-catalogue")

# Application Insights / Azure Monitor status
application_insights_configured = False
flask_request_instrumentation = False

# Configure Application Insights and explicitly instrument Flask requests.
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor()
        application_insights_configured = True
        logger.info("Azure Monitor OpenTelemetry configured")

        try:
            from opentelemetry.instrumentation.flask import FlaskInstrumentor

            FlaskInstrumentor().instrument_app(app)
            flask_request_instrumentation = True
            logger.info("Flask request instrumentation enabled")

        except Exception:
            logger.exception("Flask request instrumentation failed")

    except Exception:
        logger.exception("Azure Monitor setup failed; app will continue")

LOCAL_DATA = Path("data.json")
CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER", "travel-data")
BLOB_NAME = os.environ.get("AZURE_STORAGE_BLOB", "destinations.json")
MAX_WRITE_ATTEMPTS = 5


def sample_data():
    return [
        {
            "id": "sample-1",
            "name": "Manali",
            "country": "India",
            "category": "Mountains",
            "budget": 15000,
            "description": "A Himalayan destination with scenic valleys.",
            "created_at": "2026-09-01T10:00:00+00:00",
        },
        {
            "id": "sample-2",
            "name": "Goa",
            "country": "India",
            "category": "Beach",
            "budget": 12000,
            "description": "A coastal destination known for beaches.",
            "created_at": "2026-09-01T10:00:00+00:00",
        },
    ]


def use_azure_storage():
    return bool(os.environ.get("AZURE_STORAGE_CONNECTION_STRING"))


def get_blob_client():
    """
    Return the configured blob client.

    The container is provisioned separately. Do not try to create it
    on every page request or storage operation.
    """
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

    if not connection_string:
        raise RuntimeError("Azure Storage connection string is not configured")

    service = BlobServiceClient.from_connection_string(connection_string)
    container = service.get_container_client(CONTAINER_NAME)

    return container.get_blob_client(BLOB_NAME)


def read_blob_data(blob):
    payload = blob.download_blob().readall()
    data = json.loads(payload.decode("utf-8"))

    if not isinstance(data, list):
        raise ValueError("Blob data must be a JSON array")

    return data


def load_destinations():
    if use_azure_storage():
        blob = get_blob_client()

        try:
            return read_blob_data(blob)

        except ResourceNotFoundError:
            # The blob may not exist on the first run.
            # Create the initial data only if it is still absent.
            initial_data = sample_data()
            payload = json.dumps(initial_data, indent=2).encode("utf-8")

            try:
                blob.upload_blob(
                    payload,
                    overwrite=False,
                    content_settings=ContentSettings(
                        content_type="application/json"
                    ),
                )
            except ResourceExistsError:
                # Another request created the initial blob first.
                pass

            # Read the authoritative version from storage.
            return read_blob_data(blob)

    # Local development fallback
    if not LOCAL_DATA.exists():
        data = sample_data()
        save_destinations(data)
        return data

    data = json.loads(LOCAL_DATA.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("Local data must be a JSON array")

    return data


def save_destinations(data):
    serialized = json.dumps(data, indent=2).encode("utf-8")

    if use_azure_storage():
        blob = get_blob_client()
        blob.upload_blob(
            serialized,
            overwrite=True,
            content_settings=ContentSettings(
                content_type="application/json"
            ),
        )
    else:
        LOCAL_DATA.write_bytes(serialized)


def append_destination_concurrently(destination):
    """
    Append using ETag compare-and-swap.

    If another request changes the blob between download and upload,
    Azure rejects the stale ETag and this function retries.
    """
    if not use_azure_storage():
        data = load_destinations()
        data.append(destination)
        save_destinations(data)
        return

    blob = get_blob_client()

    for attempt in range(MAX_WRITE_ATTEMPTS):
        try:
            downloader = blob.download_blob()
            current_etag = downloader.properties.etag
            data = json.loads(downloader.readall().decode("utf-8"))

            if not isinstance(data, list):
                raise ValueError("Blob data must be a JSON array")

            data.append(destination)
            payload = json.dumps(data, indent=2).encode("utf-8")

            blob.upload_blob(
                payload,
                overwrite=True,
                etag=current_etag,
                match_condition=MatchConditions.IfNotModified,
                content_settings=ContentSettings(
                    content_type="application/json"
                ),
            )
            return

        except ResourceNotFoundError:
            # Initialize the blob if it has not been created yet.
            load_destinations()

        except ResourceModifiedError:
            logger.warning(
                "Concurrent Blob update detected; retry %s/%s",
                attempt + 1,
                MAX_WRITE_ATTEMPTS,
            )

        if attempt + 1 == MAX_WRITE_ATTEMPTS:
            raise ResourceModifiedError(
                message="Could not update destinations after retries"
            )

        time.sleep(0.05 * (attempt + 1))


@app.route("/")
def index():
    destinations = load_destinations()

    country = request.args.get("country", "").strip().lower()
    category = request.args.get("category", "").strip().lower()
    max_budget_raw = request.args.get("max_budget", "").strip()

    max_budget = None

    if max_budget_raw:
        try:
            max_budget = float(max_budget_raw)

            if max_budget < 0:
                raise ValueError

        except ValueError:
            flash("Maximum budget must be a non-negative number.", "error")
            max_budget = None

    filtered = []

    for item in destinations:
        if country and country not in item["country"].lower():
            continue

        if category and category not in item["category"].lower():
            continue

        if max_budget is not None and item["budget"] > max_budget:
            continue

        filtered.append(item)

    countries = sorted({item["country"] for item in destinations})
    categories = sorted({item["category"] for item in destinations})

    return render_template(
        "index.html",
        destinations=filtered,
        countries=countries,
        categories=categories,
        selected_country=request.args.get("country", ""),
        selected_category=request.args.get("category", ""),
        selected_budget=max_budget_raw,
    )


@app.route("/add", methods=["POST"])
def add_destination():
    name = request.form.get("name", "").strip()
    country = request.form.get("country", "").strip()
    category = request.form.get("category", "").strip()
    budget_raw = request.form.get("budget", "").strip()
    description = request.form.get("description", "").strip()

    errors = []

    if not name:
        errors.append("Destination name is required.")

    if not country:
        errors.append("Country is required.")

    if not category:
        errors.append("Category is required.")

    try:
        budget = float(budget_raw)

        if budget <= 0:
            raise ValueError

    except ValueError:
        errors.append("Budget must be a positive number.")
        budget = 0

    if len(name) > 100 or len(country) > 100 or len(category) > 50:
        errors.append("Name, country, or category is too long.")

    if len(description) > 500:
        errors.append("Description cannot exceed 500 characters.")

    if errors:
        for error in errors:
            flash(error, "error")

        return redirect(url_for("index"))

    destination = {
        "id": str(uuid.uuid4()),
        "name": name,
        "country": country,
        "category": category,
        "budget": budget,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        append_destination_concurrently(destination)

    except ResourceModifiedError:
        logger.exception("Blob remained busy after concurrent write retries")
        flash("Another update is in progress. Please submit again.", "error")
        return redirect(url_for("index")), 409

    except Exception:
        logger.exception("Failed to save destination")
        flash("Could not save the destination. Please try again.", "error")
        return redirect(url_for("index")), 500

    logger.info("Added destination id=%s", destination["id"])
    flash("Destination added successfully.", "success")

    return redirect(url_for("index"))


@app.route("/health")
def health():
    return {
        "status": "healthy",
        "storage": "azure_blob" if use_azure_storage() else "local_file",
        "application_insights": application_insights_configured,
        "flask_request_instrumentation": flask_request_instrumentation,
    }


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
