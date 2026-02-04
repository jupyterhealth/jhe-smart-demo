"""
session state management
"""

from __future__ import annotations

import secrets

from fastapi import Request
from jupyterhealth_client import JupyterHealthClient
from pydantic import BaseModel, ConfigDict
from requests_oauthlib import OAuth2Session

from .log import log
from .settings import settings

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

    model_config = ConfigDict(arbitrary_types_allowed=True)
    fhir_token: str = ""
    fhir_oauth_session: OAuth2Session | None = None
    fhir_context: dict = {}

    jhe_token: str = ""

    def get_jhe(self) -> JupyterHealthClient | None:
        """Get JupyterHealth Client object"""
        if self.jhe_token:
            return JupyterHealthClient(url=settings.jhe_url, token=self.jhe_token)
        else:
            return None

    @staticmethod
    def _is_local_iframe(request: Request) -> bool:
        if request.query_params.get("iframe"):
            return True
        return request.headers.get("sec-fetch-dest") == "iframe" and request.headers[
            "host"
        ].partition(":")[0] in {"localhost", "127.0.0.1"}

    @staticmethod
    def logout(request: Request):
        if SessionState._is_local_iframe(request):
            session_id = "iframe"
        else:
            session_id = request.session.pop(SESSION_KEY, None)
        log.info("logging out %s", session_id)
        _sessions.pop(session_id, None)

    @staticmethod
    def get_session(request: Request, make_new: bool = False) -> SessionState | None:
        """Get a single session state object

        If no session is found:

        - if not make_new: return None
        - if make_new: create and register new session id, return empty Session
        """
        session = request.session
        if SessionState._is_local_iframe(request):
            # security prevents setting for localhost iframes,
            # use 'iframe' as session id in that case
            # this doesn't affect public deployments
            log.info("Using localhost iframe session")
            session_id = "iframe"
        elif SESSION_KEY in session:
            session_id = session[SESSION_KEY]
        else:
            session_id = None

        if make_new:
            log.info("Making new Session")
            if session_id != "iframe":
                session_id = secrets.token_urlsafe(16)
                session[SESSION_KEY] = session_id
            _sessions[session_id] = s = SessionState()
            s.fhir_oauth_session = s._new_oauth_session()
            return s
        elif session_id:
            session_state = _sessions.get(session_id)
            if session_state is not None:
                return session_state
        else:
            return None

    def _new_oauth_session(self) -> OAuth2Session:
        s = OAuth2Session(
            client_id=settings.fhir_client_id,
            redirect_uri=settings.fhir_redirect_uri,
            pkce="S256",
            # TODO: configure scope
            scope="user/*.* patient/*.read openid profile launch launch/patient",
        )
        return s
