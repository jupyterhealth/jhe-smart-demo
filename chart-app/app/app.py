"""
fastapi example of SMART-on-FHIR launch

with JupyterHealth Exchange integration
"""

from __future__ import annotations

from pathlib import Path

import altair as alt
import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jupyterhealth_client import Code
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware

from .jhe import exchange_token
from .log import log
from .session import SessionState
from .settings import settings

middleware = [
    Middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        # need same-site: None for iframe EHR launch
        same_site="None",
        # should have https_only if on https (i.e. real deployment)
        # https_only=True,
    )
]

app = FastAPI(middleware=middleware)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _human_name(resource):
    """Format a FHIR resource's name"""
    name = resource.get("name")
    if not name:
        return "unknown"

    if isinstance(name, list):
        name = name[0]
    fields = name.get("given", [])
    if name.get("family"):
        fields.append(name["family"])
    if name.get("prefix"):
        fields = name["prefix"] + fields
    return " ".join(fields).strip() or "Unknown"


@app.get("/")
@app.get("/index.html")
async def index(request: Request):
    """The app's main page."""
    session = SessionState.get_session(request)
    fhir = jhe = None
    if session:
        fhir = session.fhir_context
        jhe = session.get_jhe()

    ns = {"request": request}
    ns["iframe"] = SessionState._is_local_iframe(request)

    # "ready" may be true but the access token may have expired, making fhir.patient = None
    if fhir:
        patient_id = fhir["patient"]
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{settings.fhir_api_base}/Patient/{fhir['patient']}",
                headers={
                    "Authorization": f"Bearer {fhir['access_token']}",
                    "Accept": "application/json",
                },
            )
            r.raise_for_status()
            patient = r.json()
        ns["patient_name"] = _human_name(patient)
        ns["patient_id"] = patient_id
        # medplum populates 'profile' in launch context
        # smart-on-fhir sandbox does not
        # profile = fhir.launch_context.get("profile")
        profile = None
        if not profile:
            # get profile from id token
            id_token = fhir["id_token"]
            # Spec says you can trust JWT retrieved directly from trusted IdP
            token_info = jwt.decode(id_token, options={"verify_signature": False})
            log.info(f"Attempting to fetch practitioner profile from {token_info}")
            # fhirUser="Practitioner/idnumber"
            # also available as 'profile' _sometimes_
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{settings.fhir_api_base}/{token_info['fhirUser']}",
                    headers={
                        "Authorization": f"Bearer {fhir['access_token']}",
                        "Accept": "application/json",
                    },
                )
                r.raise_for_status()
                practitioner = r.json()

            ns["practitioner_name"] = _human_name(practitioner)
            ns["practitioner_id"] = practitioner["id"]

    if jhe:
        # TODO: handle expired token
        jhe_user = jhe.get_user()
        ns["jhe_user"] = jhe_user
        if fhir:
            jhe_patient = jhe.get_patient_by_external_id(ns["patient_id"])
            ns["jhe_patient"] = jhe_patient
        else:
            jhe_patient = None
    else:
        ns["jhe_user"] = False
    return templates.TemplateResponse("chart.html", ns)


@app.get("/chart.json")
def chart_json(request: Request, iframe: bool = False):
    session = SessionState.get_session(request)
    fhir = jhe = None
    if session:
        fhir = session.fhir_context
        jhe = session.get_jhe()

    if not fhir:
        print(session)
        log.warning("No FHIR for chart")
        return None
    if not jhe:
        log.warning("No JHE for chart")
        return None

    # this is the key part of this demo:
    # - get fhir Patient
    # - lookup same patient in JHE
    # - get data from JHE
    # - plot it
    fhir_patient_id = fhir["patient"]
    jhe_patient = jhe.get_patient_by_external_id(fhir_patient_id)
    df = df = jhe.list_observations_df(
        patient_id=jhe_patient["id"], code=Code.BLOOD_GLUCOSE
    )
    # this is where the real analysis would go
    # for now, simple timeseries plot
    chart = (
        alt.Chart(df)
        .mark_line()
        .encode(
            x="effective_time_frame_date_time",
            y="blood_glucose_value",
            tooltip=["effective_time_frame_date_time", "blood_glucose_value"],
        )
        .interactive()
    )
    return chart.to_dict()


@app.get("/logout")
def logout(request: Request):
    """Logout"""
    SessionState.logout(request)
    return RedirectResponse("/")


@app.get("/launch")
async def launch(request: Request, iss: str, launch: str):
    session_state = SessionState.get_session(request, make_new=True)
    oauth = session_state.fhir_oauth_session
    authorization_url, _state = oauth.authorization_url(
        settings.fhir_smart_configuration["authorization_endpoint"],
        launch=launch,
        aud=settings.fhir_api_base,
    )

    return RedirectResponse(authorization_url)


@app.get("/callback")
async def fhir_callback(
    request: Request,
    code: str = "",
    error: str = "",
    error_description: str = "",
    state: str = "",
):
    """OAuth2 callback for FHIR"""
    session = SessionState.get_session(request)
    if session is None:
        raise HTTPException(status_code=400, detail="no session, start again.")
    oauth = session.fhir_oauth_session
    token_response = oauth.fetch_token(
        settings.fhir_smart_configuration["token_endpoint"],
        # authorization_response=str(request.url),
        code=code,
        state=state,
    )
    session.fhir_token = token_response["access_token"]
    session.fhir_context = token_response
    log.info(
        "Authenticated with %s as %s",
        settings.fhir_api_base,
        token_response.get("profile"),
    )
    # translate public issuer to
    # to private view from JHE (not needed in real public deployment)
    iss = settings.fhir_api_base.replace("localhost", "fhirproxy")

    session.jhe_token = exchange_token(settings.jhe_url, session.fhir_token, iss=iss)
    jhe = session.get_jhe()
    jhe_user = jhe.get_user()
    log.info("Authenticated with %s as %s", settings.jhe_url, jhe_user)
    return RedirectResponse("/")


def main():
    """Launches the application on port 8000 with uvicorn"""

    import uvicorn

    uvicorn.run(app, port=8000)


if __name__ == "__main__":
    main()
