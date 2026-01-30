import os
import shlex
import sys
from subprocess import check_call

from django.conf import settings
from django.utils import timezone
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
