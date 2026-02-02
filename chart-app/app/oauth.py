from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from async_lru import alru_cache
from fastapi import HTTPException

from .log import log


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
