import os.path
import time
import tomllib
import uuid
from datetime import date, datetime

import psycopg
from appdirs import AppDirs
from mastodon import Mastodon

if __name__ == '__main__':
    dirs = AppDirs("mastodon-suspension-notifier", roaming=False)
    with open(os.path.join(dirs.user_config_dir, 'config.toml'), 'r') as f:
        config = tomllib.loads(f.read())

    postgres_connection = config.get('postgres_connection')
    if not isinstance(postgres_connection, str):
        postgres_connection = input("Specify the postgres connection string: ")

    local_instance = config.get('local_instance')
    if not isinstance(local_instance, str):
        local_instance = input("What's your local instance? (Will only be used to message affected users via the API) ")

    access_token = config.get('access_token')
    if not isinstance(local_instance, str):
        access_token = input(
            "What's your access token for the API? (Will only be used to message affected users via the API) ")

    remote_instance = config.get('remote_instance')
    if not isinstance(remote_instance, str):
        remote_instance = input("What's the remote instance that will be suspended? ")

    reason = config.get('reason')
    if not isinstance(reason, str):
        reason = input("What's the reason for suspension? (Will be used in the DM, do not end it with a period) ")

    suspension_date = config.get('suspension_date')
    try:
        suspension_date = datetime.fromisoformat(suspension_date)
    except ValueError:
        print("Invalid suspension date")
    while not (isinstance(suspension_date, date)):
        suspension_date = input("What's the suspension date? (ISO 8601 format) ")
        try:
            suspension_date = datetime.fromisoformat(suspension_date)
        except ValueError:
            print("Invalid suspension date")

    follows: dict[str, set[str]] = dict()
    followers: dict[str, set[str]] = dict()
    mutuals: dict[str, set[str]] = dict()
    with psycopg.connect(postgres_connection) as conn:
        with conn.cursor() as cur:
            cur.execute(
                # Local users following remote users
                """select '@' || local.username as local, (remote.username || '@' || remote.domain) as remote
                from accounts local
                left join follows f on local.id = f.account_id
                left join accounts remote on remote.id = f.target_account_id
                where remote.domain = %(remote_instance)s and local.domain is null;""",
                {'remote_instance': remote_instance})
            for record in cur:
                local = record[0]
                remote = record[1]

                follows.setdefault(local, set()).add(remote)
        with conn.cursor() as cur:
            # Remote users following local users
            cur.execute(
                """select '@' || local.username as local, (remote.username || '@' || remote.domain) as remote
                from accounts local
                left join follows f on local.id = f.target_account_id
                left join accounts remote on remote.id = f.account_id
                where remote.domain = %(remote_instance)s and local.domain is null;""",
                {'remote_instance': remote_instance})
            for record in cur:
                local = record[0]
                remote = record[1]

                followers.setdefault(local, set()).add(remote)
    for local in follows.keys():
        if followers.get(local) is not None:
            mutuals[local] = follows[local].intersection(followers[local])
            for mutual in mutuals[local]:
                follows[local].remove(mutual)
                followers[local].remove(mutual)
    follows = {k: v for k, v in follows.items() if v is not None and len(v) > 0}
    followers = {k: v for k, v in followers.items() if v is not None and len(v) > 0}
    users = set(follows.keys())
    users.update(followers.keys())
    users.update(mutuals.keys())

    mastodon = Mastodon(
        access_token=access_token,
        api_base_url="https://{0}".format(local_instance),
        ratelimit_method="wait",
        feature_set='pleroma'
    )
    subject = "You are affected by a future suspension ({0})".format(remote_instance)
    for i, user in enumerate(users):
        mutuals_string = ""
        follows_string = ""
        followers_string = ""
        if mutuals.get(user) is not None:
            mutuals_string = ("You are currently mutuals with:\n{0}\n\n"
                              ).format("\n".join(mutuals[user]))
        if follows.get(user) is not None:
            follows_string = ("You are currently following:\n{0}\n\n"
                              ).format("\n".join(follows[user]))
        if followers.get(user) is not None:
            followers_string = ("The following users are currently following you:\n{0}\n\n"
                                ).format("\n".join(followers[user]))
        status_text = (
            "{6} Hi, you are receiving this message because you are affected by a future suspension of {0}.\n"
            "We plan to suspend {0} due to {1}.\n"
            "This is scheduled to occur on the following date: {2}.\n"
            "The suspension will have the following impacts on you:\n\n"
            "You will no longer be able to interact with any users from {0} "
            "as soon as the suspension goes into effect.\n\n"
            "The following connections will be severed:\n\n{3}{4}{5}"
            "We understand that this causes quite a bit of disruption, "
            "but we have not made this decision lightly.\n"
            "We are letting you know in advance so you can take action "
            "in order to stay in contact with these folks using alternate means."
        ).format(remote_instance, reason, suspension_date.isoformat(sep=' ', timespec='seconds'),
                 mutuals_string, follows_string, followers_string, user)
        success = False
        idempotency_key = "msn-{0}".format(str(uuid.uuid4()))
        while not success:
            try:
                print('Sending DM {0} of {1}'.format(i + 1, len(users)))
                mastodon.status_post(status_text, spoiler_text=subject, language="en", content_type='text/plain',
                                     visibility='direct', idempotency_key=idempotency_key)
                success = True
            except Exception as e:
                print(str(e))
                print("Will retry in 5 seconds.")
                time.sleep(5)
