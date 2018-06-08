"""ESI Knife."""


import os
import enum
import socket
import logging

try:
    from urllib.parse import quote
except ImportError:
    # python2
    from urllib import quote

from flask_cache import Cache
from flask import Flask


__version__ = "0.0.2"


ESI = os.environ.get("ESI_BASE_URL", "https://esi.evetech.net")

APP = Flask(__name__)
APP.error_limited = False


_GUNICORN_LOG = logging.getLogger("gunicorn.error")
APP.logger.handlers = _GUNICORN_LOG.handlers
APP.logger.setLevel(_GUNICORN_LOG.level)  # pylint: disable=no-member
LOG = APP.logger
del _GUNICORN_LOG


_REDIS_HOST = os.environ.get("ESI_KNIFE_REDIS_HOST", "redis")
_REDIS_PORT = int(os.environ.get("ESI_KNIFE_REDIS_PORT", 6379))
_TEST_SOCKET = socket.socket()

try:
    _TEST_SOCKET.connect((_REDIS_HOST, _REDIS_PORT))
except Exception as error:
    LOG.info("redis unavailable: %r", error)
    CACHE = Cache(APP, config={
        "CACHE_TYPE": "simple",
        "CACHE_DEFAULT_TIMEOUT": 300,
    })
else:
    try:
        with open("/app/redis-password", "r") as openpasswd:
            _REDIS_PASSWD = openpasswd.read().strip()
    except Exception:
        _REDIS_PASSWD = None

    CACHE = Cache(APP, config={
        "CACHE_TYPE": os.environ.get("ESI_KNIFE_CACHE_TYPE", "redis"),
        "CACHE_REDIS_URL": "redis://{}:{}/{}".format(
            _REDIS_HOST,
            _REDIS_PORT,
            os.environ.get("ESI_KNIFE_REDIS_DB", "0"),
        ),
        "CACHE_DEFAULT_TIMEOUT": 300,
        "CACHE_KEY_PREFIX": "knife.",
        "CACHE_REDIS_PASSWORD": _REDIS_PASSWD,
    })
    del _REDIS_PASSWD
finally:
    _TEST_SOCKET.close()

del _TEST_SOCKET
del _REDIS_PORT
del _REDIS_HOST


SCOPES = quote(" ".join([
    "esi-alliances.read_contacts.v1",
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-bookmarks.read_character_bookmarks.v1",
    "esi-bookmarks.read_corporation_bookmarks.v1",
    "esi-calendar.read_calendar_events.v1",
    "esi-characters.read_agents_research.v1",
    "esi-characters.read_blueprints.v1",
    "esi-characters.read_contacts.v1",
    "esi-characters.read_corporation_roles.v1",
    "esi-characters.read_fatigue.v1",
    "esi-characters.read_fw_stats.v1",
    "esi-characters.read_loyalty.v1",
    "esi-characters.read_medals.v1",
    "esi-characters.read_notifications.v1",
    "esi-characters.read_opportunities.v1",
    "esi-characters.read_standings.v1",
    "esi-characters.read_titles.v1",
    "esi-characterstats.read.v1",
    "esi-clones.read_clones.v1",
    "esi-clones.read_implants.v1",
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-corporations.read_blueprints.v1",
    "esi-corporations.read_contacts.v1",
    "esi-corporations.read_container_logs.v1",
    "esi-corporations.read_corporation_membership.v1",
    "esi-corporations.read_divisions.v1",
    "esi-corporations.read_facilities.v1",
    "esi-corporations.read_fw_stats.v1",
    "esi-corporations.read_medals.v1",
    "esi-corporations.read_outposts.v1",
    "esi-corporations.read_standings.v1",
    "esi-corporations.read_starbases.v1",
    "esi-corporations.read_structures.v1",
    "esi-corporations.read_titles.v1",
    "esi-corporations.track_members.v1",
    "esi-fittings.read_fittings.v1",
    "esi-fleets.read_fleet.v1",
    "esi-industry.read_character_jobs.v1",
    "esi-industry.read_character_mining.v1",
    "esi-industry.read_corporation_jobs.v1",
    "esi-industry.read_corporation_mining.v1",
    "esi-killmails.read_corporation_killmails.v1",
    "esi-killmails.read_killmails.v1",
    "esi-location.read_location.v1",
    "esi-location.read_online.v1",
    "esi-location.read_ship_type.v1",
    "esi-mail.read_mail.v1",
    "esi-markets.read_character_orders.v1",
    "esi-markets.read_corporation_orders.v1",
    "esi-planets.manage_planets.v1",
    "esi-planets.read_customs_offices.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-skills.read_skills.v1",
    "esi-universe.read_structures.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-wallet.read_corporation_wallets.v1",
]))


CALLBACK_URL = quote(os.environ.get(
    "KNIFE_CALLBACK_URL",
    "http://localhost:8888/callback",
))
CLIENT_ID = os.environ.get(
    "KNIFE_CLIENT_ID",
    "bfca2dd3c89a4a3bb09bdadd9e3908e8",
)
EXPOSED_URL = os.environ.get(
    "KNIFE_EXPOSED_URL",
    "http://localhost:8888",
)


class Keys(enum.Enum):
    """Redis key prefixes."""

    new = "new."
    pending = "pending."
    processing = "processing."
    rate_limit = "ratelimit."
    complete = "complete."
    alltime = "alltime."
    spec = "esijson."
