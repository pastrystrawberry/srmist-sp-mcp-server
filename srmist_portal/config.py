from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv(override=False)

PORTAL_HOST = "sp.srmist.edu.in"
PORTAL_ROOT = "https://sp.srmist.edu.in/srmiststudentportal/"
DEFAULT_LOGIN_URL = "https://sp.srmist.edu.in/srmiststudentportal/LoginServlet"
LOGIN_URL = os.getenv("SRMIST_LOGIN_URL", DEFAULT_LOGIN_URL)
HEADLESS = os.getenv("SRMIST_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}

# Fail closed if the login URL is pointed outside the known SRMIST portal origin.
from urllib.parse import urlparse
_u = urlparse(LOGIN_URL)
if _u.scheme != "https" or _u.hostname != PORTAL_HOST or not _u.path.startswith("/srmiststudentportal/"):
    raise RuntimeError("SRMIST_LOGIN_URL must be an HTTPS URL on the official SRMIST student-portal path")
