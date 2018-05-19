"""ESI-knife CLI.

Default use will cycle you through SSO and create a character_id.knife file.
If you're using this to share with a recruiter, it should be known that you
cannot trust these files after they're created. If you need 3rd party trust,
you must use the website version of this, available at https://esi.a-t.al/ or
you can host your own with instructions from https://github.com/a-tal/esi-knife

Usage:
    knife [options]

Options:
    -o FILE, --open FILE     open and display a previously created knife file
    --client-id CLIENT_ID    client ID, if override is required
    --port PORT              callback port, if override is required
"""


import os
import gzip
import json
import uuid
import base64
import codecs
import webbrowser
from http import server
from urllib import parse

import docopt

from esi_knife import ESI
from esi_knife import SCOPES
from esi_knife.utils import request_or_wait
from esi_knife.worker import get_results


def get_access_token(client_id, port):
    """Generate a new access token.

    Args:
        client_id: SSO client ID to use
        port: localhost callback port to redirect to

    Returns:
        string access token

    Raises:
        SystemExit on failure
    """

    state = str(uuid.uuid4())
    webbrowser.open((
        "https://login.eveonline.com/oauth/authorize?response_type=token"
        "&redirect_uri=http://localhost:{port}/&client_id={client_id}"
        "&scope={scopes}&state={state}"
    ).format(
        port=port,
        client_id=client_id,
        scopes=SCOPES,
        state=state,
    ))

    auth = {}

    class Callback(server.BaseHTTPRequestHandler):
        """SSO callback handler."""

        def log_message(self, *_, **__):  # pylint: disable=arguments-differ
            """Silence logging."""

            pass

        def do_GET(self):  # pylint: disable=invalid-name
            """Accept a GET request."""

            if "?" in self.path:
                query_string = dict(parse.parse_qsl(self.path.split("?")[1]))
                auth["token"] = query_string.get("access_token", None)
                msg = "<h1>Token received</h1><h2>(you can close this)</h2>"
            else:
                msg = (
                    "<script>"
                    "window.location = '/?' + window.location.hash.substr(1);"
                    "</script>"
                )

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                codecs.encode("<html><body>{}</body></html>".format(msg))
            )

    with server.HTTPServer(("localhost", port), Callback) as httpd:
        httpd.handle_request()
        httpd.handle_request()

    if not auth.get("token"):
        raise SystemExit("failed to acquire auth token")

    return auth["token"]


def verify_token(headers):
    """Verify the access token.

    Args:
        headers: http request headers

    Returns:
        integer character ID, list of scopes

    Raises:
        SystemExit on error
    """

    _, _, res = request_or_wait("{}/verify/".format(ESI), headers=headers)

    if isinstance(res, dict) and "Scopes" in res and res["Scopes"] \
            and "CharacterID" in res and res["CharacterID"]:
        return res["CharacterID"], res["Scopes"]

    raise SystemExit("Could not find scopes in access token")


def get_roles(headers, character_id):
    """Determine the character's roles.

    Args:
        headers: http request headers
        character_id: integer character ID

    Returns:
        list of roles

    Raises:
        SystemExit on error
    """

    _, _, res = request_or_wait(
        "{}/latest/characters/{}/roles/".format(ESI, character_id),
        headers=headers,
    )

    if isinstance(res, dict):
        return res.get("roles", [])

    raise SystemExit("Could not deterine character's corporation roles")


def display_results(filename):
    """Display the results from a compressed .knife file."""

    try:
        with open(filename, "r") as infile:
            data = json.loads(gzip.decompress(base64.b64decode(infile.read())))
    except Exception as error:
        raise SystemExit("Failed to read {}: {!r}".format(filename, error))

    print(json.dumps(data, indent=4, sort_keys=True))


def write_results(results, character_id):
    """Write the results to a compressed .knife file."""

    fname = "{}.knife".format(character_id)
    i = 0
    while os.path.isfile(fname):
        i += 1
        fname = "{}-{}.knife".format(character_id, i)

    with open(fname, "w") as openout:
        openout.write(codecs.decode(
            base64.b64encode(gzip.compress(codecs.encode(
                json.dumps(results),
                "utf-8",
            ))),
            "utf-8",
        ))

    print("created {}".format(fname))


def run(args):
    """Create a new knife file."""

    token = get_access_token(
        args["--client-id"] or "13927a4b444a46a3ad9a2bd99059181e",
        args["--port"] or 27392,
    )

    headers = {"Authorization": "Bearer {}".format(token)}
    character_id, scopes = verify_token(headers)

    _, _, public = request_or_wait(
        "{}/latest/characters/{}/".format(ESI, character_id)
    )

    if isinstance(public, str):
        raise SystemExit("Failed to look up public info for: {}".format(
            character_id
        ))

    roles = get_roles(headers, character_id)

    results = get_results(public, character_id, scopes, roles, headers)

    write_results(results, character_id)


def main():
    """CLI entrypoint."""

    args = docopt.docopt(__doc__)
    if args["--open"]:
        display_results(args["--open"])
    else:
        run(args)


if __name__ == "__main__":
    main()
