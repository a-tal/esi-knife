"""ESI Knife web frontend."""


from gevent import monkey
monkey.patch_all()


import uuid
from datetime import datetime

import ujson
import gevent
from flask import request
from flask import redirect
from flask import Response
from flask import render_template

from esi_knife import APP
from esi_knife import Keys
from esi_knife import CACHE
from esi_knife import SCOPES
from esi_knife import CLIENT_ID
from esi_knife import EXPOSED_URL
from esi_knife import CALLBACK_URL
from esi_knife import utils
from esi_knife import worker


@APP.route("/", methods=["GET"])
@CACHE.cached(timeout=3600)
def main_index():
    """Return the main index.html."""

    return render_template("index.html")


@APP.route("/knife", methods=["GET"])
def character_knife():
    """Start a new knife run for a character."""

    if "access_token" in request.args and "state" in request.args:
        # verify token/start knife process for character
        # do this all out of band, we might be error limited right now
        if CACHE.get("authstate.{}".format(request.args["state"])):
            CACHE.delete("authstate.{}".format(request.args["state"]))
            token = uuid.uuid4()
            CACHE.set("new.{}".format(token), request.args["access_token"])
            return redirect("/view/{}/".format(token))

    # start sso flow
    state = uuid.uuid4()
    CACHE.set("authstate.{}".format(state), "1", timeout=300)

    return redirect((
        "https://login.eveonline.com/oauth/authorize?response_type=token"
        "&redirect_uri={callback}&client_id={client}"
        "&scope={scopes}&state={state}"
    ).format(
        callback=CALLBACK_URL,
        client=CLIENT_ID,
        scopes=SCOPES,
        state=state,
    ))


@APP.route("/callback", methods=["GET"])
@CACHE.cached(timeout=3600)
def callback_route():
    """SSO callback route."""

    # this is an abusive hack
    return render_template("callback.html")


@APP.route("/view/<token>/", methods=["GET"])
def get_knife(token):
    """Direct URL access to a knife result."""

    if utils.rate_limit():
        return Response(
            "chill out bruh, maybe you need to run a self-hosted copy",
            status=420,
        )

    results = utils.get_data(token)
    if results is None:
        for state in (Keys.pending, Keys.processing, Keys.new):
            if utils.list_keys("{}{}".format(state.value, token)):
                return render_template(
                    "pending.html",
                    token=token,
                    state=state.value,
                )
        return redirect("/?e=invalid_token")

    if request.headers.get("Accept") == "application/json":
        return Response(
            ujson.dumps(results, sort_keys=True, indent=4),
            content_type="application/json",
        )

    return render_template(
        "view.html",
        data=ujson.dumps(results, sort_keys=True),
        exposed_url=EXPOSED_URL,
        token=token,
    )


@APP.route("/metrics", methods=["GET"])
@CACHE.cached(timeout=20)
def metrics_index():
    """Display some metrics."""

    return render_template(
        "metrics.html",
        new=len(utils.list_keys(Keys.new.value)),
        pending=len(utils.list_keys(Keys.pending.value)),
        processing=len(utils.list_keys(Keys.processing.value)),
        completed=len(utils.list_keys(Keys.complete.value)),
        alltime=CACHE.get(Keys.alltime.value) or 0,
        worker=not APP.knife_worker.dead,
        error_limited=APP.error_limited,
        now=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def main(debug=False):
    """Main gunicorn entrypoint."""

    APP.knife_worker = gevent.spawn(worker.main)
    APP.config["debug"] = debug
    return APP


if __name__ == "__main__":
    main(debug=True).run(host="0.0.0.0", port=8080, debug=True)
