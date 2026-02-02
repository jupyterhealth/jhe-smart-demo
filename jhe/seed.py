import json
import os
import shlex
import sys
import uuid
from subprocess import check_call

import requests
from core.models import (
    CodeableConcept,
    DataSource,
    JheUser,
    Observation,
    Organization,
    Study,
    StudyPatient,
    StudyPatientScopeConsent,
    StudyScopeRequest,
)
from django.conf import settings
from django.utils import timezone
from django.utils.crypto import get_random_string
from oauth2_provider.models import get_application_model

db = settings.DATABASES["default"]
os.environ["PGHOST"] = db["HOST"]
os.environ["PGPORT"] = db["PORT"]
os.environ["PGUSER"] = db["USER"]
os.environ["PGPASSWORD"] = db["PASSWORD"]


def sh(cmd):
    """Run a shell command"""
    print(" ".join(shlex.quote(arg) for arg in cmd))
    check_call(cmd)


sh(["dropdb", "--if-exists", db["NAME"]])
sh(["createdb", db["NAME"]])

sh([sys.executable, "manage.py", "migrate"])
sh([sys.executable, "manage.py", "seed"])

# create OAuthClient for SMART App
application = get_application_model()

application.objects.create(
    id=2,
    redirect_uris="http://localhost:8000/jhe_callback",
    client_type="public",
    authorization_grant_type="authorization-code",
    client_id=os.environ["SMART_APP_CLIENT_ID"],
    name="smart launch demo",
    user_id=None,
    skip_authorization=True,
    created=timezone.now(),
    updated=timezone.now(),
    algorithm="RS256",
    post_logout_redirect_uris="",
    hash_client_secret=True,
    allowed_origins="",
)

# copy fhir data into JHE

# 1. create an org
root_org = Organization.objects.get(id=0)
acme = Organization.objects.create(
    name="ACME Bio Research",
    type="edu",
    part_of=root_org,
)

study = Study.objects.create(
    name="Demo Study",
    description="Sample data from https://github.com/Big-Ideas-Lab/cgm-sandbox",
    organization=acme,
)

bp_code = CodeableConcept.objects.get(coding_code="omh:blood-pressure:4.0")
hr_code = CodeableConcept.objects.get(coding_code="omh:heart-rate:2.0")
cgm_code = CodeableConcept.objects.get(coding_code="omh:blood-glucose:4.0")

codes = [
    CodeableConcept.objects.get(coding_code=code)
    for code in [
        "omh:blood-pressure:4.0",
        "omh:heart-rate:2.0",
        "omh:blood-glucose:4.0",
    ]
]
for code in codes:
    StudyScopeRequest.objects.create(study=study, scope_code=code)

# get FHIR resources


def create_jhe_user_from_fhir(fhir_resource):
    user_type = fhir_resource["resourceType"].lower()
    assert user_type in {"patient", "practitioner"}

    email = None
    for t in fhir_resource["telecom"]:
        if t["system"] == "email":
            email = t["value"]
            break
    assert email is not None

    print(f"Creating {user_type}: {email}")

    given_name = " ".join(fhir_resource["name"][0]["given"])
    family_name = fhir_resource["name"][0]["family"]
    if user_type == "practitioner":
        password = "insecure-demo-password"
    else:
        password = get_random_string(length=16)
    user = JheUser.objects.create_user(
        user_type=user_type,
        email=email,
        password=password,
        first_name=given_name,
        last_name=family_name,
        identifier=str(fhir_resource["id"]),
    )
    profile = getattr(user, f"{user_type}_profile")
    profile.identifier = user.identifier
    profile.date_of_birth = fhir_resource["birthDate"]
    profile.organizations.add(acme)
    profile.save()
    return profile


fhir_url = "http://fhir:8080/fhir"
r = requests.get(f"{fhir_url}/Practitioner")
r.raise_for_status()
print(r.json())
fhir_practitioner = r.json()["entry"][0]["resource"]

r = requests.get(f"{fhir_url}/Patient")
r.raise_for_status()
fhir_patient = r.json()["entry"][0]["resource"]

practitioner = create_jhe_user_from_fhir(fhir_practitioner)
patient = create_jhe_user_from_fhir(fhir_patient)

study_patient = StudyPatient.objects.create(study=study, patient=patient)

now = timezone.now()

for code in codes:
    StudyPatientScopeConsent.objects.create(
        study_patient=study_patient,
        scope_code=code,
        consented=True,
        consented_time=now,
    )


# now upload data into the study!


def get_cgm_records():
    print("fetching cgm records")
    cgm_url = "https://github.com/Big-Ideas-Lab/cgm-sandbox/raw/b338a844357bba1e0b9f5ef5f87df51d9fdd6288/sample_subject/blood_glucose.json"

    r = requests.get(cgm_url)
    r.raise_for_status()
    cgm_records = r.json()
    cgm_records["header"]
    return cgm_records


dexcom = DataSource.objects.get(name="Dexcom")
device_id = dexcom.id

# from datetime import UTC, datetime

ns_uuid = uuid.UUID("d15785047be14c68b5a21c30e8e15c91")
ns_uuid


def uuid_for_reading(reading):
    return str(uuid.uuid5(ns_uuid, json.dumps(reading)))


def add_records(cgm_records, limit=1000):
    header = cgm_records["header"]
    n_max = len(cgm_records["body"])
    n = min(limit, n_max)
    print(f"uploading {n}/{n_max} cgm records")
    observations = []
    for reading in cgm_records["body"]:
        reading_header = header.copy()
        reading_header["uuid"] = uuid_for_reading(reading)
        record = dict(
            header=reading_header,
            body=reading,
        )
        observations.append(
            Observation(
                subject_patient=patient,
                codeable_concept=cgm_code,
                value_attachment_data=record,
            )
        )
    Observation.objects.bulk_create(observations, batch_size=100)
    print("done")


cgm_records = get_cgm_records()
add_records(cgm_records)

# at this point, we have:

# org: ACME
# - Practitioner linked with external id in FHIR
# - Patient linked with external id in FHIR
# - study patient is enrolled in
# - sample cgm data from Duke Big Ideas Lab in study
