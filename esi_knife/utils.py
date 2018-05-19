"""ESI Knife utils."""


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
from esi_knife import APP
from esi_knife import ESI
from esi_knife import LOG
from esi_knife import CACHE


EXPIRY = 604800  # 7 days


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


def get_data(uuid):
    """Open and return the character's data."""

    cache_key = "{}{}".format(Keys.complete.value, uuid)
    try:
        content = CACHE.get(cache_key)
    except Exception as error:
        LOG.warning("failed to get %s: %r", cache_key, error)
    else:
        if content is None:
            return None

        try:
            return ujson.loads(gzip.decompress(base64.b64decode(content)))
        except Exception as error:
            LOG.warning("failed to decode %s: %r", content, error)
        else:
            CACHE.cache._client.expire(cache_key, EXPIRY)

    return None


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
        CACHE.set(
            "{}{}".format(Keys.complete.value, uuid),
            codecs.decode(
                base64.b64encode(gzip.compress(codecs.encode(
                    ujson.dumps(data),
                    "utf-8",
                ))),
                "utf-8",
            ),
            timeout=EXPIRY,
        )
    except Exception as error:
        LOG.warning("Failed to save data: %r", error)


def request_or_wait(url, *args, _as_res=False, page=None, **kwargs):
    """Request the URL, or wait if we're error limited."""

    check_x_pages = True
    if page:
        kwargs["params"] = kwargs.get("params", {})
        kwargs["params"]["page"] = page
        check_x_pages = False
        LOG.warning("requesting: %s (page %d)", url, page)
    else:
        LOG.warning("requesting: %s", url)

    try:
        res = SESSION.get(url, *args, **kwargs)
        res.raise_for_status()
    except Exception as err:
        try:
            if res.status_code == 420:
                wait = int(res.headers.get("X-Esi-Error-Limit-Reset", 1)) + 1
                APP.error_limited = True
                LOG.warning("hit the error limit, waiting %d seconds", wait)
                # error limited. wait out the window then carry on
                gevent.sleep(wait)
                return request_or_wait(url, *args, _as_res=_as_res, page=page,
                                       **kwargs)
        except Exception as error:
            LOG.warning("error handling error: %r: %r", err, error)

        try:
            content = res.json()
        except Exception:
            content = res.text

        # /shrug some other error, can't win em all
        return None, url, \
            res if _as_res else "Error fetching data: {} {}".format(
                res.status_code,
                content,
            )
    else:
        if check_x_pages:
            try:
                pages = list(range(2, int(res.headers.get("X-Pages", 0))))
            except Exception as error:
                LOG.warning("error checking x-pages for %s: %r", url, error)
                pages = None
        else:
            pages = page

        return pages, url, res if _as_res else res.json()


def refresh_spec():
    """Refresh the ESI spec.

    Returns:
        dictionary: JSON loaded swagger spec
    """

    spec_details = CACHE.get(Keys.spec.value)
    if spec_details is None:
        spec_details = {"timestamp": 0}

    if time.time() - spec_details["timestamp"] > 300:
        headers = {}
        if spec_details.get("etag"):
            headers["If-None-Match"] = spec_details["etag"]

        _, _, res = request_or_wait(
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

        CACHE.set(Keys.spec.value, spec_details, timeout=3600)

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
    reqs = CACHE.get(key) or 0
    if reqs >= 20:
        return True

    CACHE.set(key, reqs + 1, timeout=60)
    return False
