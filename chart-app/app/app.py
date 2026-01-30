"""
fastapi example of SMART-on-FHIR launch

with JupyterHealth Exchange integration
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fhirclient.client import FHIRClient
from jupyterhealth_client import JupyterHealthClient
from pydantic import BaseModel
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.DEBUG)

log = logging.getLogger(__name__)

# new cookie secret on each launch
secret_key = secrets.token_hex(32)

HOST = os.environ["APP_HOST"]

smart_defaults = {
    "app_id": os.environ["FHIR_CLIENT_ID"],
    "api_base": os.environ["FHIR_API_BASE"],
    "redirect_uri": f"{HOST}/callback",
}

jhe_settings = {
    "url": os.environ["JHE_URL"],
    # "client_id": os.environ["JHE_CLIENT_ID"],
}


middleware = [
    Middleware(
        SessionMiddleware,
        secret_key=secret_key,
        # should have https_only if on https (i.e. real deployment)
        # https_only=True,
    )
]

app = FastAPI(middleware=middleware)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# in memory storage of state
# this should be out-of-memory (e.g. db) for multiple replicas, etc.
_sessions = {}


SESSION_KEY = "session_id"


class SessionState(BaseModel):
    """A session's credentials

    Stores oauth credentials for both the SMART-on-FHIR launch
    and the JupyterHealth Exchange endpoint.

    Can't afford to put fhir state in starlette Session middleware,
    since it's too big for a cookie.
    """

    fhir_state: dict = {}
    jhe_token: str = ""

    def _save_fhir_state(self, state):
        """Persist updates to the state

        for later deserialization
        """
        self.fhir_state = state

    def get_fhir(self) -> FHIRClient | None:
        if self.fhir_state:
            return FHIRClient(state=self.fhir_state, save_func=self._save_fhir_state)
        else:
            return None

    def new_fhir(self, settings) -> FHIRClient:
        """Reset and create new FHIR state

        At the start of SMART on FHIR launch.
        """
        self.fhir_state = {}
        client = FHIRClient(settings=settings, save_func=self._save_fhir_state)
        self._save_fhir_state(client.state)
        return client

    def get_jhe(self) -> JupyterHealthClient | None:
        """Get JupyterHealth Client object"""
        if self.jhe_token:
            return JupyterHealthClient(token=self.jhe_token)
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
        session_state = _sessions.get(session_id)
        if session_state is not None:
            return session_state

    if make_new:
        session_id = secrets.token_urlsafe(16)
        session[SESSION_KEY] = session_id
        _sessions[session_id] = s = SessionState()
        return s
    else:
        return None


def _get_fhir(session) -> FHIRClient | None:
    """Get the current session's FHIRClient, if any"""
    state = _get_session(session)
    if state is None:
        return None
    return state.get_fhir()


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


@app.get("/")
@app.get("/index.html")
async def index(request: Request):
    """The app's main page."""
    session = _get_session(request.session)
    fhir = jhe = None
    if session:
        fhir = session.get_fhir()
        jhe = session.get_jhe()

    ns = {"request": request}

    # "ready" may be true but the access token may have expired, making fhir.patient = None
    if fhir and fhir.ready and fhir.patient is not None:
        ns["patient_name"] = fhir.human_name(
            fhir.patient.name[0]
            if fhir.patient.name and len(fhir.patient.name) > 0
            else "Unknown"
        )
        ns["patient_id"] = fhir.patient_id

        profile = fhir.launch_context["profile"]
        ns["practitioner_name"] = profile.get("display", "unknown")
        ns["practitioner_id"] = profile["reference"]

    jhe = _get_jhe(request.session)
    if jhe:
        jhe_user = jhe.get_user()
        print(jhe_user)
        ns["jhe_user"] = jhe_user
        ns["jhe_name"] = jhe_user[""]
    else:
        ns["jhe_user"] = False
    return templates.TemplateResponse("chart.html", ns)


@app.get("/callback")
def fhir_callback(request: Request, code: str):
    """OAuth2 callback for FHIR"""
    fhir = _get_fhir(request.session)
    if fhir is None:
        print(request.session)
        print(_sessions)
        raise HTTPException(status_code=400, detail="no session, start again.")
    try:
        fhir.handle_callback(str(request.url))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return RedirectResponse("/")


@app.get("/logout")
def logout(request: Request):
    """Logout"""
    _logout(request.session)
    return RedirectResponse("/")


@app.get("/launch")
def launch(request: Request, iss: str, launch: str):
    session_state = _get_session(request.session, make_new=True)
    # reset for new launch
    smart_settings = {}
    smart_settings.update(smart_defaults)
    smart_settings["api_base"] = iss
    smart_settings["launch_token"] = launch
    fhir = session_state.new_fhir(smart_settings)
    assert _get_fhir(request.session) is not None
    auth_url = fhir.authorize_url
    log.info("redirecting to %s", auth_url)
    return RedirectResponse(auth_url)


# JHE handlers

# @app.get("/jhe-login")
# async def jhe_login(request: Request):
# session = _get_session(request.session, make_new=True)


# @app.get("/chart.json")
# def chart_json():
# return {}
# df = get_data()
# chart = alt.Chart(df)...
# return chart.to_dict()


def main():
    """Launches the application on port 8000 with uvicorn"""

    import uvicorn

    uvicorn.run(app, port=8000)


if __name__ == "__main__":
    main()
