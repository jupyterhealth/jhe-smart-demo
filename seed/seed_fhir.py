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

    # TODO: allow reset, until then delete fhir resource
    # to clear
    existing: dict[str, list[dict]] = {}
    for p in (seed_path / "fhir").glob("*.json"):
        with p.open() as f:
            resource_text = f.read()

        resource = json.loads(resource_text)
        resource_type = resource["resourceType"]
        if resource_type not in existing:
            url = f"{fhir_url}/{resource_type}"
            print(f"getting {url}")
            r = requests.get(url)
            if r.status_code >= 400:
                print(r.json())
                r.raise_for_status()
            existing[resource_type] = [e["resource"] for e in r.json().get("entry", [])]

        # only handle resources with names,
        # assume names are unique
        # future: deeper match
        if any(found["name"] == resource["name"] for found in existing[resource_type]):
            print(f"Already have {resource_type}: {resource['name']}")
        else:
            url = f"{fhir_url}/{resource_type}"
            print(f"putting {p} -> {url}")
            r = requests.post(
                f"{fhir_url}/{resource['resourceType']}",
                data=resource_text,
                headers={"Content-Type": "application/json"},
            )
            if r.status_code >= 400:
                print(r.json())
                r.raise_for_status()


if __name__ == "__main__":
    seed_fhir()
