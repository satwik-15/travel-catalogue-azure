import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from azure.storage.blob import BlobServiceClient, ContentSettings
from azure.core.exceptions import ResourceNotFoundError

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("travel-catalogue")

LOCAL_DATA = Path("data.json")
CONTAINER_NAME = os.environ.get("AZURE_STORAGE_CONTAINER", "travel-data")
BLOB_NAME = os.environ.get("AZURE_STORAGE_BLOB", "destinations.json")


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
    service = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    )
    container = service.get_container_client(CONTAINER_NAME)
    try:
        container.create_container()
    except Exception:
        pass
    return container.get_blob_client(BLOB_NAME)


def load_destinations():
    if use_azure_storage():
        blob = get_blob_client()
        try:
            payload = blob.download_blob().readall()
            return json.loads(payload.decode("utf-8"))
        except ResourceNotFoundError:
            data = sample_data()
            save_destinations(data)
            return data

    if not LOCAL_DATA.exists():
        data = sample_data()
        save_destinations(data)
        return data

    return json.loads(LOCAL_DATA.read_text(encoding="utf-8"))


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

    destinations = load_destinations()
    destinations.append(
        {
            "id": str(uuid.uuid4()),
            "name": name,
            "country": country,
            "category": category,
            "budget": budget,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    save_destinations(destinations)
    logger.info("Added destination: %s", name)
    flash("Destination added successfully.", "success")
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return {"status": "healthy", "storage": "azure_blob" if use_azure_storage() else "local_file"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
