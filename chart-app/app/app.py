"""
fastapi example of SMART-on-FHIR launch

with JupyterHealth Exchange integration
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

import altair as alt
import httpx
import jwt
from async_lru import alru_cache
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jupyterhealth_client import Code, JupyterHealthClient
from pydantic import BaseModel, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.DEBUG)

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Load settings from environment"""

    model_config = SettingsConfigDict(env_file=".env")

    app_host: str = "http://127.0.0.1:8000"
    secret_key: str = secrets.token_hex(32)
    fhir_client_id: str
    fhir_api_base: str

    jhe_url: str
    jhe_client_id: str
    jhe_public_url: str = ""

    @field_validator("jhe_public_url", mode="before")
    @classmethod
    def _public_url(
        cls,
        v: str,
        values,
    ) -> str:
        if not v:
            return values.data["jhe_url"]
        return v

    @computed_field
    def fhir_redirect_uri(self) -> str:
        return f"{self.app_host}/callback"

    @computed_field
    def jhe_redirect_uri(self) -> str:
        return f"{self.app_host}/jhe_callback"


settings = Settings()

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


# in memory storage of state
# this should be out-of-memory (e.g. db) for multiple replicas, etc.
_sessions: dict[str, SessionState] = {}


SESSION_KEY = "session_id"


class SessionState(BaseModel):
    """A session's credentials

    Stores oauth credentials for both the SMART-on-FHIR launch
    and the JupyterHealth Exchange endpoint.

    Can't afford to put fhir state in starlette Session middleware,
    since it's too big for a cookie.
    """

    fhir_token: str = ""
    fhir_api: str = ""
    fhir_oauth_state: str = ""
    fhir_code_verifier: str = ""
    fhir_launch: str = ""
    fhir_context: dict = {}

    jhe_token: str = ""
    jhe_oauth_state: str = ""
    jhe_code_verifier: str = ""

    # async def get_fhir(self, path):
    def get_fhir(self):
        return None

    def get_jhe(self) -> JupyterHealthClient | None:
        """Get JupyterHealth Client object"""
        if self.jhe_token:
            return JupyterHealthClient(url=settings.jhe_url, token=self.jhe_token)
        else:
            return None


def _get_session(session: dict, make_new: bool = False) -> SessionState | None:
    """Get a single session state object

    If no session is found:

    - if not make_new: return None
    - if make_new: create and register new session id, return empty Session
    """
    if SESSION_KEY in session:
        session_id = session[SESSION_KEY]

    # fake persistent session id
    # needed for iframe credentials
    session_id = "session_id"
    if session_id:
        session_state = _sessions.get(session_id)
        if session_state is not None:
            return session_state

    if make_new:
        # session_id = secrets.token_urlsafe(16)
        session[SESSION_KEY] = session_id
        _sessions[session_id] = s = SessionState()
        return s
    else:
        return None


def _get_jhe(session) -> JupyterHealthClient | None:
    """Get the current session's JupyterHealthClient, if any"""
    state = _get_session(session)
    if state is None:
        return None
    return state.get_jhe()


def _logout(session):
    """Logout, clearing credentials from both FHIR and JHE"""
    session_id = session.pop(SESSION_KEY, None)
    _sessions.pop(session_id, None)


def _human_name(resource):
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
    session = _get_session(request.session)
    fhir = jhe = None
    if session:
        fhir = session.fhir_context
        jhe = session.get_jhe()

    ns = {"request": request}

    # "ready" may be true but the access token may have expired, making fhir.patient = None
    if fhir:
        patient_id = fhir["patient"]
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{session.fhir_api}/Patient/{fhir['patient']}",
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
                    f"{session.fhir_api}/{token_info['fhirUser']}",
                    headers={
                        "Authorization": f"Bearer {fhir['access_token']}",
                        "Accept": "application/json",
                    },
                )
                r.raise_for_status()
                practitioner = r.json()

            ns["practitioner_name"] = _human_name(practitioner)
            ns["practitioner_id"] = practitioner["id"]

    jhe = _get_jhe(request.session)
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
def chart_json(request: Request):
    session = _get_session(request.session)
    fhir = jhe = None
    if session:
        fhir = session.fhir_context
        jhe = session.get_jhe()

    if not fhir and jhe:
        return None
    fhir_patient_id = fhir["patient"]
    jhe_patient = jhe.get_patient_by_external_id(fhir_patient_id)
    df = df = jhe.list_observations_df(
        patient_id=jhe_patient["id"], code=Code.BLOOD_GLUCOSE
    )
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
    _logout(request.session)
    return RedirectResponse("/")


@app.get("/launch")
async def launch(request: Request, iss: str, launch: str):
    session_state = _get_session(request.session, make_new=True)
    session_state.fhir_api = iss.rstrip("/")
    assert session_state is not None
    print("launch!")
    url, state, code_verifier = await _openid_authorize_redirect(
        iss,
        client_id=settings.fhir_client_id,
        redirect_uri=settings.fhir_redirect_uri,
        scope="user/*.* patient/*.read openid profile launch launch/patient",
        extra_params={"launch": launch, "aud": session_state.fhir_api},
    )
    session_state.fhir_code_verifier = code_verifier
    session_state.fhir_oauth_state = state
    return RedirectResponse(url)


@app.get("/callback")
async def fhir_callback(
    request: Request,
    code: str = "",
    error: str = "",
    error_description: str = "",
    state: str = "",
):
    """OAuth2 callback for FHIR"""
    session = _get_session(request.session)
    if session is None:
        raise HTTPException(status_code=400, detail="no session, start again.")

    check_state = session.fhir_oauth_state
    session.fhir_oauth_state = ""
    code_verifier = session.fhir_code_verifier
    session.fhir_code_verifier = ""
    token_response = await _openid_token_callback(
        session.fhir_api,
        settings.fhir_client_id,
        code,
        error,
        error_description,
        state,
        check_state,
        code_verifier,
        extra_params={
            "redirect_uri": settings.fhir_redirect_uri,
            "state": state,
        },
    )
    print(token_response)
    session.fhir_context = token_response
    session.fhir_token = token_response["access_token"]
    log.info(
        "Authenticated with %s as %s", session.fhir_api, token_response.get("profile")
    )
    return RedirectResponse("/")


# JHE handlers


def _generate_pkce_params():
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    code_challenge_base64 = (
        base64.urlsafe_b64encode(code_challenge).decode("utf-8").rstrip("=")
    )
    return code_verifier, code_challenge_base64


@alru_cache
async def _get_openid_config(openid_base_uri):
    async with httpx.AsyncClient() as client:
        url = f"{openid_base_uri.rstrip('/')}/.well-known/openid-configuration"
        try:
            r = await client.get(url)
            r.raise_for_status()
        except Exception as e:
            # if not
            url = urlunparse(
                urlparse(openid_base_uri)._replace(
                    path="/.well-known/openid-configuration"
                )
            )
            try:
                r = await client.get(url)
                r.raise_for_status()
            except Exception as e2:
                raise e from None

        config = r.json()
    return config


async def _openid_authorize_redirect(
    openid_base_uri, client_id, redirect_uri, scope, extra_params=None
):
    state = secrets.token_urlsafe(16)
    openid_config = await _get_openid_config(openid_base_uri)
    authorize_url = openid_config["authorization_endpoint"]
    code_verifier, code_challenge = _generate_pkce_params()

    authorize_params = {
        "state": state,
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if extra_params:
        authorize_params.update(extra_params)
    url = f"{authorize_url}?{urlencode(authorize_params)}"
    log.info("Redirecting to %s", url)
    return url, state, code_verifier


@app.get("/jhe_login")
async def jhe_login(request: Request) -> None:
    session = _get_session(request.session, make_new=True)
    assert session is not None
    if session.jhe_token:
        jhe = session.get_jhe()
        assert jhe is not None
        try:
            user = jhe.get_user()
        except Exception:
            log.error("Failed to get JHE user")
            session.jhe_token = ""
        else:
            # logged in, go back home
            return RedirectResponse("/")

    url, state, code_verifier = await _openid_authorize_redirect(
        f"{settings.jhe_public_url}/o",
        client_id=settings.jhe_client_id,
        scope="openid",
        redirect_uri=settings.jhe_redirect_uri,
    )
    # begin OAuth redirect
    session.jhe_oauth_state = state
    session.jhe_code_verifier = code_verifier
    return RedirectResponse(url)


async def _openid_token_callback(
    openid_base_uri,
    client_id,
    code,
    error,
    error_description,
    state,
    check_state,
    code_verifier,
    extra_params=None,
):
    openid_config = await _get_openid_config(openid_base_uri)
    token_url = openid_config["token_endpoint"]

    if error:
        raise HTTPException(500, error_description)
    if not code:
        raise HTTPException(400, "Missing code= parameter")

    # token_url = f"{settings.jhe_url}/o/token/"
    if not state:
        raise HTTPException(400, "OAuth state missing")
    if state != check_state:
        raise HTTPException(400, "OAuth state doesn't match")

    params = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        # client_secret if confidential
        "code_verifier": code_verifier,
    }
    if extra_params:
        params.update(extra_params)

    async with httpx.AsyncClient() as client:
        r = await client.post(
            token_url,
            data=params,
        )
        if r.status_code >= 400:
            print(r.json())
            r.raise_for_status()
        token_response = r.json()

    return token_response


@app.get("/jhe_callback")
async def jhe_callback(
    request: Request,
    code: str = "",
    error: str = "",
    error_description: str = "",
    state: str = "",
):
    """Complete OAuth callback"""
    session = _get_session(request.session)
    if session is None:
        raise HTTPException(400, "No session, login again")

    check_state = session.jhe_oauth_state
    session.jhe_oauth_state = ""
    code_verifier = session.jhe_code_verifier
    session.jhe_code_verifier = ""
    token_response = await _openid_token_callback(
        f"{settings.jhe_public_url}/o",
        settings.jhe_client_id,
        code,
        error,
        error_description,
        state,
        check_state,
        code_verifier,
    )
    session.jhe_token = token_response["access_token"]
    jhe = session.get_jhe()
    assert jhe is not None
    user = jhe.get_user()
    log.info("Authenticated with JHE as %s", user)
    return RedirectResponse("/")


def main():
    """Launches the application on port 8000 with uvicorn"""

    import uvicorn

    uvicorn.run(app, port=8000)


if __name__ == "__main__":
    main()
