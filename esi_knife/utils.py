"""ESI Knife utils."""


import os
import gzip
import time
import base64
import codecs

import ujson
import gevent
import requests
from flask import request
from jsonderef import JsonDeref
from requests.adapters import HTTPAdapter

from esi_knife import __version__
from esi_knife import Keys
from esi_knife import ESI
from esi_knife import LOG
from esi_knife import DATA
from esi_knife import CACHE


def new_session():
    """Build a new requests.Session object."""

    session = requests.Session()
    session.headers["User-Agent"] = "ESI-knife/{}".format(__version__)
    session.mount(
        "https://",
        HTTPAdapter(max_retries=3, pool_connections=10, pool_maxsize=100),
    )
    return session


SESSION = new_session()


def get_json(url, params=None, wait=True):
    """Request the url with our requests session (GET only)."""

    try:
        res = SESSION.get(url, params=params or {})
        res.raise_for_status()
        return url, res.json()
    except Exception as error:
        if wait and (res.status_code == 420):
            # error limited. wait out the window then carry on
            gevent.sleep(
                int(res.headers.get("X-Esi-Error-Limit-Reset", 1)) + 1
            )
            return get_json(url, params=params)
        try:
            content = res.json()
        except Exception:
            try:
                content = res.text
            except Exception:
                content = None

        return url, "Error fetching data: {} {}".format(
            res.status_code,
            content,
        )


def get_data(filename, decompress=True):
    """Open and return the content from file."""

    filepath = os.path.join(DATA, filename)
    try:
        with open(filepath, "r") as openfile:
            content = openfile.read()
    except Exception as error:
        LOG.warning("failed to open %s: %r", filename, error)
    else:
        try:
            if decompress:
                return ujson.loads(gzip.decompress(base64.b64decode(content)))
            return ujson.loads(content)
        except Exception as error:
            LOG.warning("failed to decode %s: %r", filename, error)

    return None


def list_data():
    """Return a list of known data files."""

    # TODO: should probably put this in a real db...
    return [x for x in os.listdir(DATA) if not x.startswith(".")]


def list_keys(prefix):
    """Return all keys with the given prefix."""

    prefix_len = len(CACHE.cache.key_prefix)
    return [
        codecs.decode(x)[prefix_len:]
        for x in CACHE.cache._client.keys(  # pylint: disable=W0212
            "{}{}*".format(CACHE.cache.key_prefix, prefix)
        )
    ]


def write_data(uuid, data):
    """Try to store the data, log errors."""

    try:
        _write_data(uuid, data)
    except Exception as error:
        LOG.warning("Failed to save data: %r", error)


def _write_data(uuid, data):
    """Store the data."""

    with open(os.path.join(DATA, uuid), "w") as openresult:
        openresult.write(codecs.decode(
            base64.b64encode(gzip.compress(codecs.encode(
                ujson.dumps(data),
                "utf-8",
            ))),
            "utf-8",
        ))


def request_or_wait(url, *args, _as_res=False, **kwargs):
    """Request the URL, or wait if we're error limited."""

    try:
        LOG.warning("requesting: %s", url)
        res = SESSION.get(url, *args, **kwargs)
        res.raise_for_status()
        return url, res if _as_res else res.json()
    except Exception as err:
        try:
            if res.status_code == 420:
                wait = int(res.headers.get("X-Esi-Error-Limit-Reset", 1)) + 1
                LOG.warning("hit the error limit, waiting %d seconds", wait)
                # error limited. wait out the window then carry on
                gevent.sleep(wait)
                return request_or_wait(url, *args, _as_res=_as_res, **kwargs)
        except Exception as error:
            LOG.warning("error handling error: %r: %r", err, error)

        try:
            content = res.json()
        except Exception:
            content = res.text

        # /shrug some other error, can't win em all
        return url, res if _as_res else "Error fetching data: {} {}".format(
            res.status_code,
            content,
        )


def refresh_spec():
    """Refresh the ESI spec.

    Returns:
        dictionary: JSON loaded swagger spec
    """

    spec_file = os.path.join(DATA, ".esi.json")
    if os.path.isfile(spec_file):
        with open(spec_file, "r") as open_spec:
            spec_details = ujson.loads(open_spec.read())
    else:
        spec_details = {"timestamp": 0}

    if time.time() - spec_details["timestamp"] > 300:
        headers = {}
        if spec_details.get("etag"):
            headers["If-None-Match"] = spec_details["etag"]

        _, res = request_or_wait(
            "{}/latest/swagger.json".format(ESI),
            _as_res=True,
            headers=headers,
        )

        if isinstance(res, str):
            LOG.warning("failed to refresh spec: %s", res)
            return spec_details.get("spec", {})

        spec_details["timestamp"] = time.time()

        if res.status_code != 304:
            spec_details["etag"] = res.headers.get("ETag")
            spec_details["spec"] = JsonDeref().deref(res.json())

        with open(spec_file, "w") as new_spec:
            new_spec.write(ujson.dumps(spec_details))

    return spec_details["spec"]


def get_ip():
    """Return the requestor's IP."""

    if "X-Forwarded-For" in request.headers:
        x_forwarded_for = request.headers["X-Forwarded-For"].split(",")
        try:
            return x_forwarded_for[-2].strip()
        except IndexError:
            return x_forwarded_for[0].strip()
    elif "X-Real-Ip" in request.headers:
        return request.headers["X-Real-Ip"]
    else:
        return request.remote_addr


def rate_limit():
    """Apply a rate limit."""

    key = "".join((Keys.rate_limit.value, get_ip()))
    requests = CACHE.get(key) or 0
    if requests >= 5:
        return True

    CACHE.set(key, requests + 1, timeout=60)
    return False
