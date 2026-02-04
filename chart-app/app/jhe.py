import requests


def exchange_token(jhe_url, access_token, iss):
    """
    RFC 8693 OAuth token exchange

    https://datatracker.ietf.org/doc/html/rfc8693
    """
    r = requests.post(
        f"{jhe_url}/o/token-exchange",
        data={
            "subject_token": access_token,
            "iss": iss,
            "audience": jhe_url,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        },
    )
    print(r.status_code, r.text)
    if r.status_code >= 400:
        print(r.json())
        r.raise_for_status()
    token_info = r.json()
    return token_info["access_token"]
