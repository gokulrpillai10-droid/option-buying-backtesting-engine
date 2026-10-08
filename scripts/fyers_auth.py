

#!/usr/bin/env python3
"""
Fyers Daily Token Refresh
Run this once every morning before fetching data or placing orders.
Opens the Fyers login page in your browser, waits for you to log in,
then auto-saves the fresh access_token to .env

Mirrors the Zerodha flow (tradealgo/zerodha_auth.py): Fyers access tokens
expire daily, so this exchanges a fresh auth_code for a new access_token and
caches it back into .env. The data loader / order layer reads FYERS_ACCESS_TOKEN
from there and never runs this interactive handshake itself.

Prereqs (one-time):
  pip3 install fyers-apiv3 python-dotenv
  Create an API app at https://myapi.fyers.in/dashboard/ and copy the app_id +
  secret_id + redirect URI into .env (see .env.example).
"""

import os
import sys
from urllib.parse import urlparse, parse_qs

try:
    from dotenv import load_dotenv, set_key
except ImportError:
    sys.exit("Missing python-dotenv. Run: pip3 install python-dotenv")

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    sys.exit("Missing fyers-apiv3. Run: pip3 install fyers-apiv3")

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

APP_ID       = os.getenv("FYERS_APP_ID")        # e.g. "L9NY9ULP0X-100"
SECRET_ID    = os.getenv("FYERS_SECRET_ID")
REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI")  # must EXACTLY match the app config

missing = [k for k, v in {
    "FYERS_APP_ID": APP_ID,
    "FYERS_SECRET_ID": SECRET_ID,
    "FYERS_REDIRECT_URI": REDIRECT_URI,
}.items() if not v]
if missing:
    sys.exit(
        
        "Missing in .env: " + ", ".join(missing) +
        "\nCreate an app at https://myapi.fyers.in/dashboard/ and fill .env "
        "(see .env.example)."
    )

session = fyersModel.SessionModel(
    client_id=APP_ID,
    secret_key=SECRET_ID,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code",
)

login_url = session.generate_authcode()
print("\n" + "=" * 60)
print("  FYERS TOKEN REFRESH")
print("=" * 60)
print(f"\nOpening browser -> {login_url}\n")
try:
    import webbrowser
    webbrowser.open(login_url)
except Exception:
    pass

print("After logging in (+ 2FA/PIN), you'll be redirected to a URL like:")
print(f"  {REDIRECT_URI}?s=ok&code=200&auth_code=XXXXXXXX&state=...")
print("\nThe page may fail to load — that's fine. Copy the FULL URL from the")
print("address bar and paste it here, then press Enter:")
redirect = input("  URL: ").strip()

# Extract auth_code from redirect URL (or accept a bare pasted code).
try:
    params = parse_qs(urlparse(redirect).query)
    auth_code = params["auth_code"][0]
except Exception:
    auth_code = redirect.strip()

print("\nExchanging auth_code for access_token...")
try:
    session.set_token(auth_code)
    resp = session.generate_token()
    access_token = resp.get("access_token")
    if not access_token:
        sys.exit(f"  No access_token in response: {resp}")

    set_key(ENV_PATH, "FYERS_ACCESS_TOKEN", access_token)
    print("  Access token saved -> .env")
    print(f"  Token: {access_token[:16]}...")

    # Quick verify
    fyers = fyersModel.FyersModel(client_id=APP_ID, token=access_token, is_async=False)
    profile = fyers.get_profile()
    if profile.get("s") == "ok":
        d = profile.get("data", {})
        print(f"  Verified — logged in as: {d.get('name')} ({d.get('fy_id')})")
        print("\n  Ready to fetch data / place orders.\n")
    else:
        print(f"  Token saved but profile check returned: {profile}")
except SystemExit:
    raise
except Exception as e:
    print(f"  Error: {e}")
    sys.exit(1)
