"""Authorise this project's osu! API client as the player, once, for the endpoints that need it.

Everything else here runs on client credentials, which can only ever be `public`. The friends
list is the first thing that cannot: it belongs to a user, so it needs the authorisation-code
flow, where the player signs in on osu.ppy.sh and grants the scopes to this client by hand.

Scopes asked for, and why each:

  * identify      -- /me, so a token can say whose it is. Granted by default anyway.
  * public        -- everything client credentials already reach, under the same token.
  * friends.read  -- /friends. The reason this file exists.

Nothing that writes. chat.read is the only other read scope and nothing here wants it.

The app's callback URL on osu.ppy.sh/home/account/edit (OAuth section) must equal REDIRECT
exactly, port and path included, or the authorise page refuses before it asks anything.

    python3 tools/reference/authorize.py [--redirect http://localhost:7270/callback]

Opens the browser, waits for the redirect on localhost, trades the code for a token and saves
it beside the client credentials. user_token() reads it back and refreshes it when stale, so
this only needs running again if the refresh token is revoked or the scopes change.
"""
import http.server, json, os, secrets, sys, time, urllib.parse, urllib.request, webbrowser

REDIRECT = "http://localhost:7270/callback"
SCOPES = "identify public friends.read"
STORE = os.path.expanduser("~/.config/osu-api-user.json")


def credentials():
    """Client id and secret, from the environment or the same env files recap.py reads."""
    env = dict(os.environ)
    for path in (".osu-api.env", os.path.expanduser("~/.config/osu-api.env")):
        try:
            for line in open(path):
                k, _, v = line.strip().partition("=")
                if k and not k.startswith("#"):
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass
    return env["OSU_API_CLIENT"], env["OSU_API_KEY"]


def exchange(fields):
    client, secret = credentials()
    body = urllib.parse.urlencode({"client_id": client, "client_secret": secret, **fields}).encode()
    request = urllib.request.Request("https://osu.ppy.sh/oauth/token", data=body,
                                     headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        grant = json.load(response)

    grant["expires_at"] = time.time() + grant.pop("expires_in") - 60
    # The secret's file is the model: owner-only, never in the repo.
    fd = os.open(STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(grant, f, indent=1)
    return grant


def user_token():
    """A bearer token for the player, refreshed if it has expired. None if never authorised."""
    try:
        grant = json.load(open(STORE))
    except OSError:
        return None
    if time.time() >= grant["expires_at"]:
        grant = exchange({"grant_type": "refresh_token", "refresh_token": grant["refresh_token"]})
    return grant["access_token"]


def main():
    redirect = sys.argv[sys.argv.index("--redirect") + 1] if "--redirect" in sys.argv else REDIRECT
    target = urllib.parse.urlsplit(redirect)
    client, _ = credentials()
    state = secrets.token_urlsafe(16)
    caught = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != target.path:
                self.send_response(404)
                self.end_headers()
                return
            caught.update(urllib.parse.parse_qsl(url.query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab.\n")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer((target.hostname, target.port or 80), Callback)
    url = "https://osu.ppy.sh/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client, "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPES, "state": state})

    print(f"Authorise in the browser; if it does not open, visit:\n\n  {url}\n")
    webbrowser.open(url)
    while not caught:
        server.handle_request()

    if caught.get("state") != state:
        sys.exit("state mismatch: the redirect did not come from this authorisation")
    if "code" not in caught:
        sys.exit(f"refused: {caught.get('error')} {caught.get('error_description', '')}".strip())

    grant = exchange({"grant_type": "authorization_code", "code": caught["code"], "redirect_uri": redirect})
    request = urllib.request.Request("https://osu.ppy.sh/api/v2/me",
                                     headers={"Authorization": f"Bearer {grant['access_token']}",
                                              "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        me = json.load(response)
    print(f"authorised as {me['username']} ({me['id']}), scopes: {grant.get('scope', SCOPES)} -> {STORE}")


if __name__ == "__main__":
    main()
