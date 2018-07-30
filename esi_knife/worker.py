"""Background processor for ESI knife."""


import re
import gc
import copy
import random
from traceback import format_exception
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor

import gevent

from esi_knife import LOG
from esi_knife import ESI
from esi_knife import Keys
from esi_knife import CACHE
from esi_knife import utils


WORKERS = []
ADDITIONAL_PARAMS = {
    "character_id": {
        "event_id": "/characters/{character_id}/calendar/",
        "contract_id": "/characters/{character_id}/contracts/",
        "fitting_id": "/characters/{character_id}/fittings/",
        "label_id": "/characters/{character_id}/mail/labels/",
        "planet_id": "/characters/{character_id}/planets/",
        "mail_id": "/characters/{character_id}/mail/",
    },
    "corporation_id": {
        "observer_id": "/corporation/{corporation_id}/mining/observers/",
        "contract_id": "/corporations/{corporation_id}/contracts/",
        "starbase_id": "/corporations/{corporation_id}/starbases/",
        "division": "/corporations/{corporation_id}/wallets/",
    },
}


def process_new():
    """Process all new tokens, verify or we're done early."""

    for new_key in utils.list_keys(Keys.new.value):
        uuid = new_key.split(".")[-1]
        LOG.info("processing new uuid: %r", uuid)

        token = CACHE.get(new_key)
        CACHE.delete(new_key)

        if not token:
            LOG.warning("no token stored for uuid: %r", uuid)
            continue

        pending_key = "{}{}".format(Keys.pending.value, uuid)
        CACHE.set(
            pending_key,
            "1",
            timeout=70,
        )
        headers = {"Authorization": "Bearer {}".format(token)}
        _, _, res = utils.request_or_wait(
            "{}/verify/".format(ESI),
            headers=headers,
        )

        failed = False
        if isinstance(res, str) or "CharacterID" not in res:
            utils.write_data(uuid, {"auth failure": res})
            failed = True
        else:
            _, _, roles = utils.request_or_wait(
                "{}/latest/characters/{}/roles/".format(
                    ESI,
                    res["CharacterID"],
                ),
                headers=headers,
            )
            if isinstance(roles, str):
                utils.write_data(uuid, {"roles failure": roles})
                failed = True

        CACHE.delete(pending_key)

        if not failed:
            CACHE.set(
                "{}{}".format(Keys.processing.value, uuid),
                res["CharacterID"],
                timeout=7200,
            )

            WORKERS.append(
                gevent.spawn(knife, uuid, token, res, roles)
            )


def build_urls(scopes, roles, spec,  # pylint: disable=R0914,R0912
               known_params, all_params):
    """Return a list of applicable URLs to fetch."""

    ignored = [
        "/loyalty/stores/{corporation_id}/offers/",
        "/characters/{character_id}/search/",
        "/corporations/{corporation_id}/contracts/{contract_id}/bids/",
        "/corporations/{corporation_id}/contracts/{contract_id}/items/",
        "/characters/{character_id}/opportunities/",
    ]

    urls = []

    for route, methods in spec["paths"].items():  # pylint: disable=R1702
        if "get" not in methods or route in ignored:
            continue

        oper = methods["get"]

        if any(x not in roles for x in oper.get("x-required-roles", [])):
            # we don't have the corporate roles for this route
            continue

        required_scopes = oper.get("security", [{}])[0].get("evesso", [])
        if any(x not in scopes for x in required_scopes):
            # our access token doesn't have this scope
            continue

        params = {}
        unknown_path_params = []

        for param in oper["parameters"]:
            if param["in"] == "path":
                if param["name"] in known_params:
                    params[param["name"]] = known_params[param["name"]]
                else:
                    unknown_path_params.append(param["name"])

        fan_out_requests = {}
        for param in unknown_path_params:
            for known_param in params:
                if param in all_params.get(known_param, {}):
                    fan_out_requests[param] = all_params[known_param][param]

        if len(fan_out_requests) != len(unknown_path_params):
            # some route we don't have access to fan out on
            continue

        param_sets = []
        if fan_out_requests:
            for param, entities in fan_out_requests.items():
                to_remove = []
                for nested in param_sets:
                    if param not in nested:
                        to_remove.append(nested)
                        for _id in entities:
                            updated_set = {k: v for k, v in nested.items()}
                            updated_set[param] = _id
                            param_sets.append(updated_set)

                for updated in to_remove:
                    param_sets.remove(updated)

                for _id in entities:
                    param_set = {k: v for k, v in params.items()}
                    param_set[param] = _id
                    param_sets.append(param_set)

        elif params:
            param_sets.append(params)

        else:
            # no parameters, this route has no relevance then
            continue

        for param_set in param_sets:
            urls.append("{}{}{}".format(
                ESI,
                spec["basePath"],
                route.format(**param_set),
            ))

    return urls


def expand_params(scopes, roles, spec,  # pylint: disable=R0914,R0913
                  known_params, all_params, headers):
    """Gather IDs from all_params into known_params."""

    errors = []
    purge = {x: [] for x in all_params}

    transform = {
        "/characters/{character_id}/mail/labels/": \
            lambda x: [i["label_id"] for i in x["labels"]],
        "/characters/{character_id}/planets/": \
            lambda x: [i["planet_id"] for i in x],
        "/characters/{character_id}/calendar/": \
            lambda x: [i["event_id"] for i in x],
        "/characters/{character_id}/contracts/": \
            lambda x: [i["contract_id"] for i in x],
        "/characters/{character_id}/mail/": \
            lambda x: [i["mail_id"] for i in x],
        "/corporations/{corporation_id}/calendar/": \
            lambda x: [i["event_id"] for i in x],
        "/corporations/{corporation_id}/contracts/": \
            lambda x: [i["contract_id"] for i in x],
    }

    expansion_results = {}

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {}
        for parent, id_types in all_params.items():
            for id_type, url in id_types.items():
                oper = spec["paths"][url]["get"]
                required_roles = oper.get("x-required-roles", [])
                if any(x not in roles for x in required_roles):
                    # we don't have the corporate roles for this route
                    purge[parent].append(id_type)
                    continue

                required_sso = oper.get("security", [{}])[0].get("evesso", [])
                if any(x not in scopes for x in required_sso):
                    # our access token doesn't have this scope
                    purge[parent].append(id_type)
                    continue

                path = "https://esi.evetech.net/latest{}".format(
                    url.format(**known_params)
                )
                futures[pool.submit(
                    utils.request_or_wait,
                    path,
                    headers=headers,
                )] = (url, parent, id_type)

        pages = {}
        while True:
            completed = []
            expansion = {}
            for future in as_completed(futures):
                completed.append(future)
                templated_url, parent, id_type = futures[future]
                page, url, data = future.result()
                page_key = (templated_url, parent, id_type, url)

                if page and isinstance(page, list):
                    pages[page_key] = {1: data}
                    for _page in page:
                        expansion[pool.submit(
                            utils.request_or_wait,
                            url,
                            page=_page,
                            headers=headers,
                        )] = (templated_url, parent, id_type)
                elif isinstance(page, int):
                    if isinstance(data, list):
                        pages[page_key][page] = data
                    else:
                        LOG.warning("worker page expansion error: %r", data)
                else:
                    if templated_url in transform:
                        expansion_results[url] = data
                        all_params[parent][id_type] = transform[templated_url](
                            data
                        )
                    elif isinstance(data, list):
                        all_params[parent][id_type] = data
                    else:
                        LOG.warning("worker expansion error: %r", data)

            for complete in completed:
                futures.pop(complete)
            futures.update(expansion)

            if not futures:
                break

        for details, page_data in pages.items():
            templated_url, parent, id_type, url = details
            data = []
            for page in sorted(page_data):
                data.extend(page_data[page])
            if not data:
                continue
            if templated_url in transform:
                expansion_results[url] = data
                try:
                    all_params[parent][id_type] = transform[templated_url](
                        data
                    )
                except Exception as error:
                    LOG.warning(
                        "failed to transform %s. error: %r data: %r",
                        url,
                        error,
                        data,
                    )
            else:
                all_params[parent][id_type] = data

        for parent, purged_ids in purge.items():
            for purged_id in purged_ids:
                all_params[parent].pop(purged_id)

    if errors:
        LOG.warning("worker errors: %s", " ".join(errors))

    return expansion_results


def _get_all_data(scopes, roles, known_params, all_params, headers):
    """Retrieve all data for the parameters."""

    spec = utils.refresh_spec()
    results = expand_params(
        scopes,
        roles,
        spec,
        known_params,
        all_params,
        headers,
    )

    urls = build_urls(scopes, roles, spec, known_params, all_params)

    page_expansions = {}  # {url: {page: results}}

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = []
        for url in urls:
            futures.append(pool.submit(
                utils.request_or_wait,
                url,
                headers=headers,
            ))

        while True:
            expansion_requests = []
            completed_futures = []
            for future in as_completed(futures):
                completed_futures.append(future)
                pages, url, result = future.result()
                if pages and isinstance(pages, list):
                    page_expansions[url] = {1: result}
                    for page in pages:
                        expansion_requests.append(pool.submit(
                            utils.request_or_wait,
                            url,
                            page=page,
                            headers=headers,
                        ))
                elif isinstance(pages, int):
                    page_expansions[url][pages] = result
                else:
                    results[url] = result

            for complete in completed_futures:
                futures.remove(complete)
            futures.extend(expansion_requests)

            if not futures:
                break

    for url, pages in page_expansions.items():
        data = []
        for page in sorted(pages):
            data.extend(pages[page])
        if data:
            results[url] = data

    return results


# attribute keys that can be resolved via /universe/names/
ID_KEYS = [
    "type_id",
    "creator_id",
    "creator_corporation_id",
    "executor_corporation_id",
    "contact_id",
    "alliance_id",
    "corporation_id",
    "issuer_corporation_id",
    "issuer_id",
    "ship_type_id",
    "installer_id",
    "blueprint_type_id",
    "product_type_id",
    "solar_system_id",
    # "from",  /mail/, can include mailing lists though
    # "recipient_id",  /mail/, can include mailing lists though
    # "sender_id",  /notifications/, can include factions though
    "region_id",
    # "planet_id",  use /universe/planets/{planet_id}/
    "skill_id",
    # "first_party_id",  includes factions
    # "second_party_id",  includes factions
    "tax_receiver_id",
    "client_id",
    "ceo_id",
    "home_station_id",
    "assignee_id",
]

# TODO
LOCATION_ID_KEYS = [
    "location_id",
    "end_location_id",
    "start_location_id",
    "blueprint_location_id",
    "facility_id",
    "output_location_id",
    # "station_id",  # in industry/jobs, double check if this can be a cit
]

RAW_ID_KEYS = [
    re.compile(r".*/alliances/(?P<alliance_id>[0-9]+)/corporations/$"),
    re.compile(r".*/characters/(?P<character_id>[0-9]+)/implants/$"),
    re.compile(r".*/corporations/(?P<corporation_id>[0-9]+)/members/$"),
]


def _recurse_for_ids(data):
    """Find as many ids as we can."""

    ids = []

    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, int) and key in ID_KEYS:
                ids.append(val)
            elif isinstance(val, (list, dict)):
                ids.extend(_recurse_for_ids(val))

    elif isinstance(data, list):
        for item in data:
            ids.extend(_recurse_for_ids(item))

    return ids


def _get_all_ids(results):
    """Pull all the ids from results."""

    ids = []

    for route, data in results.items():
        for route_re in RAW_ID_KEYS:
            if re.match(route_re, route) and isinstance(data, list):
                ids.extend(data)
                break
        else:
            ids.extend(_recurse_for_ids(data))

    return list(set(ids))  # lazy de-dupe


def _recurse_apply_ids(data, ids):
    """Apply name keys for found ids."""

    if isinstance(data, dict):
        for key in list(data.keys()):
            if isinstance(data[key], int):
                if key in ID_KEYS and data[key] in ids:
                    data["{}_name".format(key)] = ids[data[key]]
            elif isinstance(data[key], (list, dict)):
                _recurse_apply_ids(data[key], ids)

    elif isinstance(data, list):
        for item in data:
            _recurse_apply_ids(item, ids)


def _apply_all_ids(results, ids):
    """Apply keys to raw results then recurse into the rest."""

    for route in list(results.keys()):
        for route_re in RAW_ID_KEYS:
            if re.match(route_re, route) and isinstance(results[route], list):
                normalized = []
                for item in results[route]:
                    new_item = {"id": item}
                    if item in ids:
                        new_item["name"] = ids[item]
                    normalized.append(new_item)
                results[route] = normalized
                break
        else:
            _recurse_apply_ids(results[route], ids)


def _get_names(ids):
    """Resolve ids to names."""

    resolved = {}
    failed = []
    for i in range(0, len(ids), 1000):
        batch = ids[i:i+1000]
        _, _, res = utils.request_or_wait(
            "{}/latest/universe/names/".format(ESI),
            method="post",
            json=batch,
        )
        if isinstance(res, list):
            for _res in res:
                resolved[_res["id"]] = _res["name"]
        else:
            failed.extend(batch)

    while failed:
        still_failed = []
        random.shuffle(failed)
        batch_size = max(min(int(len(failed) / 2), 500), 1)
        for i in range(0, len(failed), batch_size):
            batch = failed[i:i+batch_size]

            _, _, res = utils.request_or_wait(
                "{}/latest/universe/names/".format(ESI),
                method="post",
                json=batch,
            )
            if isinstance(res, list):
                for _res in res:
                    resolved[_res["id"]] = _res["name"]
            else:
                still_failed.extend(batch)

        failed = still_failed

        if batch_size == 1 and still_failed:
            LOG.warning("failed to resolve: %r", still_failed)
            break

    return resolved


def _add_names(results):
    """Best-effort resolve IDs to names."""

    _apply_all_ids(results, _get_names(_get_all_ids(results)))


def get_results(public, character_id, scopes, roles, headers):
    """Expand parameters and fetch all results."""

    all_params = copy.deepcopy(ADDITIONAL_PARAMS)

    known_params = {"character_id": character_id}

    if public["corporation_id"] > 2000000:
        known_params["corporation_id"] = public["corporation_id"]
    else:
        all_params.pop("corporation_id")

    if "alliance_id" in public:
        known_params["alliance_id"] = public["alliance_id"]

    results = _get_all_data(scopes, roles, known_params, all_params, headers)
    _add_names(results)
    return results


def knife(uuid, token, verify, roles):  # pylint: disable=R0914
    """Pull all ESI data for a character_id.

    Args:
        uuid: string uuid token
        token: SSO access token
        verify: dictionary return from /verify/
        roles: list of corporation roles
    """

    character_id = verify["CharacterID"]
    LOG.info("knife run started for character: %s", character_id)

    scopes = verify["Scopes"]

    _, _, public = utils.request_or_wait(
        "{}/latest/characters/{}/".format(ESI, character_id)
    )

    if isinstance(public, str):
        CACHE.delete("{}{}".format(Keys.processing.value, uuid))
        utils.write_data(uuid, {"public info failure": public})
        return

    headers = {"Authorization": "Bearer {}".format(token)}
    results = get_results(public, character_id, scopes, roles, headers)

    utils.write_data(uuid, results)
    CACHE.delete("{}{}".format(Keys.processing.value, uuid))
    CACHE.cache.inc(Keys.alltime.value, 1)
    LOG.info("completed character: %r", character_id)


def main():
    """Main worker entrypoint."""

    LOG.info("knife worker online")

    # until we can resume jobs
    for state in (Keys.processing.value, Keys.pending.value):
        CACHE.delete_many(*utils.list_keys(state))

    while True:
        prune = []

        for glet in WORKERS:
            if glet.successful():
                prune.append(glet)
            elif glet.dead:
                LOG.warning(
                    "worker crashed: %s",
                    "".join(format_exception(*glet.exc_info)).strip(),
                )
                prune.append(glet)

        for glet in prune:
            WORKERS.remove(glet)

        process_new()

        gc.collect()
        gevent.sleep(10)
