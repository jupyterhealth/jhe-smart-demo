"""
Seed the FHIR database

Uploads all json records in seed/fhir/*.json
"""

import json
from pathlib import Path

import requests

fhir_url = "http://localhost:4040/fhir"


def seed_fhir():
    seed_path = Path(__file__).parent

    for p in (seed_path / "fhir").glob("*.json"):
        with p.open() as f:
            resource_text = f.read()

        resource = json.loads(resource_text)
        resource_type = resource["resourceType"]
        assert "id" in resource  # need id for re-upload
        url = f"{fhir_url}/{resource_type}"
        if "id" in resource:
            url = f"{url}/{resource['id']}"
            send = requests.put
        else:
            send = requests.post
        print(f"putting {p} -> {url}")

        r = send(
            url,
            data=resource_text,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code >= 400:
            print(r.json())
            r.raise_for_status()


if __name__ == "__main__":
    seed_fhir()
