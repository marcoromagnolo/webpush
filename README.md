webpush
=======

Web Push notifications of many websites from one installation: the visitors of a site subscribe from their browser,
the messages are queued (console or REST API) and sent at the times of the schedule of the site.

* **Console** (web app): Home with the counters and today's sendings, Messages (queue, editor with preview, send now,
  test), Subscribers, Schedules, Sites (settings, VAPID keys, integration code, test browsers), API tokens,
  IP blacklist, Errors. Messages, Subscribers and Schedules show always one site, chosen at the top of the page
  (Site): the sites stay apart. Home and Errors show one site or all the sites.
* **REST API** `/api/v1/sites/<code>/...`: the browsers subscribe (public, only from the origins of the site), the CMS
  of the sites queue the messages (with a token).
* **Scheduler**: at every slot of the schedule of a site (default the 5 best times of the day) the first message of
  its queue is sent to all the subscribers (see *Sending*).

One process (Flask + waitress, Python 3.14) in a Docker container, also in local; the database is MariaDB on the host.
Layout, stack and scripts are the ones of scrapynews.

Contents: *Requirements* · *Configuration* · *Install* (local and server) · *Tests with curl* · *Troubleshooting* ·
*Upgrade from the single site version* · *Updates* · *Sending* · *REST API* · *Integration in a site* · *Operations* ·
*VAPID keys* · *Development*

Requirements
============

* Python 3.12 or newer only for the automatic tests (the container has Python 3.14)
* MariaDB 10.6 or newer
* Docker, also on your computer (the application runs only in its container)
* A browser with Web Push: Chrome, Edge, Firefox; Safari 16+ on macOS, on iOS only for a site added to the Home screen
* In production: the console and the API reachable on **https** by the browsers of the sites (nginx in front, e.g.
  `https://www.example.com/notify`). The browsers subscribe only on https pages (or `localhost`), the console needs
  https (or `localhost`) for the test browsers.

Configuration
=============

Secrets and environment-specific values live in `.env` (never committed). Copy `.env.example` to `.env` and fill in
the values:

| Variable | Default | |
|---|---|---|
| `DB_HOST`, `DB_PORT` | `localhost`, `3306` | MariaDB server (`127.0.0.1` in the container) |
| `DB_USER`, `DB_PASSWORD`, `DB_NAME` | required | user, password and database (written by the install block, *Install*) |
| `DB_POOL_SIZE`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE` | `10`, `10`, `1800` | connection pool (`dbpool.py`): max connections, seconds to wait for one, seconds after which an idle one is reopened |
| `DB_CONNECT_TIMEOUT`, `DB_READ_TIMEOUT` | `10`, `60` | seconds to wait for a connection, for a query |
| `WEB_HOST`, `WEB_PORT` | `127.0.0.1`, `8080` | address of the console and the API |
| `WEB_THREADS` | `8` | threads of the web server (waitress) |
| `WEB_DEBUG` | `false` | `true` only in development: Flask server with the debugger, never in production |
| `WEB_BEHIND_PROXY` | `false` | `true` behind nginx: real client ip, https urls, path prefix from `X-Forwarded-Prefix` |
| `PUBLIC_URL` | `http://WEB_HOST:WEB_PORT` | url of the application as the browsers of the sites reach it (integration code, click tracking) |
| `JWT_SECRET_KEY` | required | secret of the logins of the console, at least 32 bytes (`openssl rand -base64 48`) |
| `PUSH_WORKERS`, `PUSH_TIMEOUT` | `10`, `10` | parallel requests to the push services, seconds to wait for each |
| `PUSH_MAX_FAILURES` | `5` | failures in a row before a subscriber is removed (404 / 410 remove it at once) |
| `DEFAULT_SITE` | empty | code of the site of the old endpoints `/subscription/` and `/push_message/` (empty: disabled) |
| `ERROR_LOG_RETENTION_DAYS`, `ERROR_LOG_MAX_PER_PROCESS` | `30`, `500` | errors of the page **Errors**: days they are kept, max saved by the process |
| `REFRESH_SECONDS` | `15` | automatic refresh of the pages **Home**, **Messages**, **Subscribers** and **Errors** (`0` = off); each page has its own choice, kept by the browser |
| `LOG_LEVEL` | `INFO` | level of the log files in `log/` |

The settings of each site (url, origins, VAPID keys, contact, time to live, default icon and badge,
schedule) are in the database, managed in the console (pages **Sites** and **Schedules**).
The schedules are in local time: the `TZ` of the container (`Europe/Rome`), of the computer in local.

Install
=======

The application runs only in Docker, also on your computer: `docker-start.sh` builds the image, (re)creates the
container `webpush` and waits until it answers; `docker-stop.sh` stops it. One installation serves all the sites.

| | Your computer | Server |
|---|---|---|
| clone of the repository | e.g. `~/Projects/webpush` | `/srv/webpush/app` (user `webpush`) |
| `.env` | configuration and secrets | same |
| `log/`, `venv/` | mounted in the container | same |
| database / MariaDB user of the app | `webpush` / `webpush_app@127.0.0.1` | same |
| Docker image and container | `webpush` | `webpush` |
| console and API | `http://127.0.0.1:8090` | `127.0.0.1:8080`, published by nginx on https |

The container uses the host network: it reaches MariaDB at `127.0.0.1:3306` and listens on `127.0.0.1:WEB_PORT`.
The MariaDB user of the app is `@'127.0.0.1'`, not `@'localhost'`: the container connects over TCP to 127.0.0.1, and
with `skip_name_resolve` (default of many MariaDB installations) a user `@'localhost'` matches only the connections
from the unix socket (error 1698 *Access denied*, whatever the password).

Python dependencies are not part of the repository nor of the image: at every start of the container
`install_deps.sh` creates the venv (volume `venv/`) and installs `requirements.txt`, only the first time and when
`requirements.txt` or the Python version change.

Local installation (your computer)
----------------------------------

From zero to a notification received by your browser. Port **8090**: 8080 is often taken (e.g. by a local nginx).

1. **Requirements**: Docker usable without sudo (your user in the group `docker`) and MariaDB running:

       docker ps > /dev/null && echo "docker ok"
       systemctl is-active mariadb

2. **Code**:

       git clone https://github.com/marcoromagnolo/webpush.git ~/Projects/webpush
       cd ~/Projects/webpush

3. **Database, MariaDB user and `.env`** (needs `sudo` for MariaDB). The password of the user is generated and
   written only in `.env` (never shown); `.env` is created from `.env.example` with the local settings and a generated
   `JWT_SECRET_KEY`. The block can be run again: the user gets a new password, the other values of an existing `.env`
   stay (then recreate the container: `./docker-start.sh`, `.env` is read only when the container is created):

       cd ~/Projects/webpush
       DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
       sudo mariadb <<SQL
       CREATE DATABASE IF NOT EXISTS webpush DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
       CREATE USER IF NOT EXISTS 'webpush_app'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
       ALTER USER 'webpush_app'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
       GRANT ALL PRIVILEGES ON webpush.* TO 'webpush_app'@'127.0.0.1';
       SQL
       if [ -f .env ]; then
           sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASS|" .env
       else
           sed -e "s|^DB_HOST=.*|DB_HOST=127.0.0.1|" \
               -e "s|^DB_USER=.*|DB_USER=webpush_app|" \
               -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASS|" \
               -e "s|^DB_NAME=.*|DB_NAME=webpush|" \
               -e "s|^WEB_PORT=.*|WEB_PORT=8090|" \
               -e "s|^PUBLIC_URL=.*|PUBLIC_URL=http://127.0.0.1:8090|" \
               -e "s|^WEB_BEHIND_PROXY=.*|WEB_BEHIND_PROXY=false|" \
               -e "s|^LOG_LEVEL=.*|LOG_LEVEL=DEBUG|" \
               -e "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -base64 48 | tr -d '/+=\n')|" \
               .env.example > .env
       fi
       chmod 600 .env
       unset DB_PASS
       # check: it prints webpush_app@127.0.0.1, the credentials of the application are right
       MYSQL_PWD="$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)" \
           mariadb -h 127.0.0.1 -u webpush_app -N -e "SELECT CURRENT_USER();"

   A database `webpush` of the single site version already there? `sudo mariadb webpush -e "SHOW TABLES"`: with
   `messages` and `subscribers` see *Upgrade from the single site version* (or, to throw it away in local,
   `sudo mariadb -e "DROP DATABASE webpush"` and run the block again).

4. **Tables**, only at the first installation:

       MYSQL_PWD="$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)" \
           mariadb -h 127.0.0.1 -u webpush_app webpush < sql/create_tables.sql
       MYSQL_PWD="$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)" \
           mariadb -h 127.0.0.1 -u webpush_app -N webpush -e "SHOW TABLES"

   10 tables: `api_token`, `click`, `delivery`, `error_log`, `ip_blacklist`, `message`, `schedule_slot`, `site`,
   `subscriber`, `user`.

5. **Start**:

       ./docker-start.sh

   It ends with `webpush started: web app on 127.0.0.1:8090` (the first start installs the Python dependencies: a
   few minutes). When the app doesn't start it prints the last output of the container and exits with an error.
   Check:

       curl http://127.0.0.1:8090/health            # {"ok":true}: application and database answer
       docker logs --tail 50 webpush                # output of the container
       tail -f log/app.log                          # logs of the application (also notifier.log, scheduler.log, api.log)

6. **Administrator**: open <http://127.0.0.1:8090> (exactly this address, the `PUBLIC_URL`): the first access opens
   **Setup**, choose username and password (at least 8 characters). The next accesses go to **Login**.

7. **A test site**: **Sites > New site** (every field of the forms has an icon *i*: hover or Tab on it for what the
   field does, its format and an example)

   | Field | Value |
   |---|---|
   | Name | `Demo` |
   | Code | `demo` |
   | Url | `https://www.example.com` (any: the test page of the console doesn't need it) |
   | Contact | `mailto:<your address>` |
   | Generate new VAPID keys | checked |

   The site is created with its VAPID keys and the default schedule (every day 07:30 12:30 15:30 18:30 21:00, changed
   in the page Schedules), and becomes the site of the pages (Site, at the top of the pages).

8. **A real notification in the browser**: **Sites > Demo > Test page** (or the button *Test page* in the list of the
   sites). It is a page of the console with the code of **Integration** (`webpush-client.js`, `webpush-sw.js`, public
   API), as on the real site, and a **debug log** that shows every step: what the browser does, what the server
   answers, what the push service answers, what the service worker receives.

   1. **Enable notifications**, then **Allow**. The log shows the steps (service worker registered, permission, VAPID
      key, subscription created on the push service, `POST /subscriptions 201`) and the state becomes *subscribed*;
      the page **Subscribers** counts this browser.
   2. **1. Diagnose**: checks browser, origin, permission, keys of the site, API, service worker, subscription in the
      browser and on the server (failures and last answer of the push service). Every error has a line `→` with what
      to do.
   3. **2. Send a test notification to this browser**: sent through the server only to this browser. The log shows the
      answer of the push service (status, reason, body, time) and the events of the service worker: *push received*,
      *notification shown* (or *NOT shown* and why).
   4. A real message: **Messages > New message** (title, text, link), **Add to the queue**, then in its page
      **Send now to all**. The table of the sendings shows `1/1` delivered.
   5. Click on the notification: the link opens, the log shows *notification clicked* and **Home** counts the click.

   If something fails: **Copy** the log (it has all the details) and see *Troubleshooting*.

9. **Test browser and scheduled sending**:
   * **Sites > Demo > Integration > Use this browser as test browser**: this browser receives the **Send test** of the
     messages (button in the page of a message), which doesn't change their status.
   * **Schedules**: add to today a time 2 minutes from now (e.g. `07:30 12:30 15:42 18:30 21:00`), **Save schedule**.
     At that minute the first message of the queue is sent to all the subscribers: **Home > Today** shows it.

10. **API**: **API tokens**, client `curl`, site `Demo`, **Generate token** (shown only once), then the
    *Tests with curl* below.

11. **Stop and start again**:

        ./docker-stop.sh        # stops the container: it stays stopped, also after a reboot
        ./docker-start.sh       # new container: after a change of .env or of the code

Server setup (once per server, as an administrator)
----------------------------------------------------

1. Packages:

       sudo apt update
       sudo apt install -y docker.io mariadb-server mariadb-client gh git nginx

2. User `webpush`, a system user (no password, not a sudoer) whose home is `/srv/webpush`, allowed to use Docker
   (note: the group `docker` is equivalent to root on the host):

       sudo adduser --system --group --home /srv/webpush --shell /bin/bash webpush
       sudo chmod 750 /srv/webpush
       sudo usermod -aG docker webpush

   An administrator opens a shell as `webpush` with `sudo -iu webpush`; for a direct SSH access put a public key in
   `/srv/webpush/.ssh/authorized_keys` (owned by `webpush`, mode 600, directory 700).

3. MariaDB account `webpush`, authenticated by the Linux user (unix socket, no password): it creates the database
   `webpush` and the user of the application `webpush_app`, and can only use the database `webpush`.
   `wait_timeout` of MariaDB (default 8 hours) must stay above `DB_POOL_RECYCLE`. The `ALTER USER` is needed on a
   server of the single site version: its `sql/create_db.sql` created `'webpush'@'localhost'` **with a password**,
   `CREATE USER IF NOT EXISTS` leaves it as it is and then `mariadb` as the user `webpush` answers
   `ERROR 1045 (28000): Access denied for user 'webpush'@'localhost' (using password: NO)` (the old application
   stops working with that password: it is replaced, *Upgrade from the single site version*):

       sudo mariadb <<'SQL'
       CREATE USER IF NOT EXISTS 'webpush'@'localhost' IDENTIFIED VIA unix_socket;
       ALTER USER 'webpush'@'localhost' IDENTIFIED VIA unix_socket;
       GRANT CREATE USER ON *.* TO 'webpush'@'localhost';
       GRANT ALL PRIVILEGES ON `webpush`.* TO 'webpush'@'localhost' WITH GRANT OPTION;
       SQL
       # check: it prints webpush@localhost
       sudo -iu webpush mariadb -N -e "SELECT CURRENT_USER();"

4. GitHub login of the user `webpush` (the token is saved in `/srv/webpush/.config/gh`):

       sudo -iu webpush gh auth login

Server installation (as the user `webpush`)
-------------------------------------------

Steps 1-5 run in a shell of the user `webpush`, without sudo. `/srv/webpush` belongs to `webpush` with mode 750
(*Server setup*, step 2): your administrator user can't enter it (`cd /srv/webpush/app`: *Permission denied*) and
must not create anything there with `sudo` (the files would belong to root). From your administrator user open the
shell of `webpush` first:

    sudo -iu webpush        # the prompt becomes webpush@...; exit goes back to your user

Every block of commands is complete: a new shell doesn't keep the variables of the previous steps. Step 6 (nginx)
needs an administrator: `exit` first.

1. Code:

       gh repo clone marcoromagnolo/webpush /srv/webpush/app

2. Database, MariaDB user and `.env`. The account `webpush` connects without password (*Server setup*, step 3). The
   password of the MariaDB user of the application is generated and written only in `.env` (never shown). The user is
   `@'127.0.0.1'`, not `@'localhost'`: the container connects over TCP to 127.0.0.1, and with `skip_name_resolve`
   (default of many MariaDB installations) a user `@'localhost'` matches only the connections from the unix socket
   (error 1698 *Access denied*, whatever the password). The block creates `.env` from the template with the database,
   the port, the production settings and a generated `JWT_SECRET_KEY`. It can be run again: the user gets a new
   password and only `DB_PASSWORD` of the existing `.env` changes (then recreate the container, step 4: `--env-file`
   is read only by `docker run`):

       cd /srv/webpush/app
       DB_PASS=$(openssl rand -base64 24 | tr -d '/+=')
       mariadb <<SQL
       CREATE DATABASE IF NOT EXISTS webpush DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;
       CREATE USER IF NOT EXISTS 'webpush_app'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
       ALTER USER 'webpush_app'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
       GRANT ALL PRIVILEGES ON webpush.* TO 'webpush_app'@'127.0.0.1';
       DROP USER IF EXISTS 'webpush_app'@'localhost';
       SQL
       if [ -f .env ]; then
           sed -i "s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASS|" .env
       else
           sed -e "s|^DB_HOST=.*|DB_HOST=127.0.0.1|" \
               -e "s|^DB_USER=.*|DB_USER=webpush_app|" \
               -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$DB_PASS|" \
               -e "s|^DB_NAME=.*|DB_NAME=webpush|" \
               -e "s|^WEB_PORT=.*|WEB_PORT=8080|" \
               -e "s|^PUBLIC_URL=.*|PUBLIC_URL=https://www.example.com/notify|" \
               -e "s|^WEB_BEHIND_PROXY=.*|WEB_BEHIND_PROXY=true|" \
               -e "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -base64 48 | tr -d '/+=\n')|" \
               .env.example > .env
       fi
       chmod 600 .env
       unset DB_PASS
       # check: it prints webpush_app@127.0.0.1, the credentials of the application are right
       MYSQL_PWD="$(grep '^DB_PASSWORD=' .env | cut -d= -f2-)" \
           mariadb -h 127.0.0.1 -u webpush_app -N -e "SELECT CURRENT_USER();"

   `ERROR 1045 ... 'webpush'@'localhost' (using password: NO)` at the first `mariadb`: the account `webpush` still has
   the password of the single site version, `ALTER USER` of *Server setup*, step 3.

   Then set `PUBLIC_URL` (the https address of the application, e.g. `https://www.example.com/notify`) and, for a
   site of the single site version, `DEFAULT_SITE`:

       nano /srv/webpush/app/.env

3. Tables, only at the first installation:

       mariadb webpush < /srv/webpush/app/sql/create_tables.sql

   A database of the single site version: *Upgrade from the single site version* instead.

4. Start:

       /srv/webpush/app/docker-start.sh

   It ends with `webpush started: web app on 127.0.0.1:8080`. The container restarts with the server
   (`--restart unless-stopped`, 1 CPU, 512 MB of memory).

5. Check: `curl http://127.0.0.1:8080/health` (`{"ok":true}`).

6. nginx (**as an administrator**: `exit` from the shell of `webpush`): the application on a path of a site with https, e.g. `/notify` of
   `www.example.com` (in its `server` block):

       location /notify/ {
           proxy_pass http://127.0.0.1:8080/;
           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_set_header X-Forwarded-Host $host;
           proxy_set_header X-Forwarded-Prefix /notify;
       }

   Behind the Cloudflare proxy use `X-Forwarded-For $http_cf_connecting_ip` and `X-Forwarded-Proto https` (otherwise
   the ip blacklist sees the ips of Cloudflare). Then `sudo nginx -t && sudo systemctl reload nginx` and
   `curl https://www.example.com/notify/health`.

7. Right after the deploy open `https://www.example.com/notify/` and create the administrator: until then anyone
   reaching the console could create it. Then **Sites > New site**, **Integration** (code for the site),
   **API tokens** (token for the CMS), **Schedules**; the **Test page** of the site checks everything from your
   browser.

Tests with curl
===============

With the application started, a site `demo` (url `https://www.example.com`) and a token of the site:

    export B=http://127.0.0.1:8090
    export TOKEN=wp_...
    export SITE=demo
    export ORIGIN=https://www.example.com      # the url of the site (or the console: http://127.0.0.1:8090)

**Application**

    curl -s $B/health
    # {"ok":true}

**Public API** (used by the browsers: `webpush-client.js`, `webpush-sw.js`)

    # key to subscribe
    curl -s $B/api/v1/sites/$SITE/vapid-public-key
    # {"public_key":"BEl6..."}

    # a site that doesn't exist (or disabled)
    curl -s $B/api/v1/sites/nope/vapid-public-key
    # {"error":"Unknown site"}

    # subscription of a browser (here a fake one): only from an origin of the site
    curl -s -X POST $B/api/v1/sites/$SITE/subscriptions \
      -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
      -d '{"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/curl-test", "expirationTime": null,
           "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                    "auth": "tBHItJI5svbpez7KI4CCXg"}}}'
    # {"subscribed":true}   (HTTP 201; the same endpoint again updates it, no duplicate)

    # without Origin, or from another origin
    curl -s -X POST $B/api/v1/sites/$SITE/subscriptions -H "Origin: https://evil.example" \
      -H "Content-Type: application/json" -d '{}'
    # {"error":"Origin not allowed"}   (HTTP 403)

    # preflight CORS of the browsers
    curl -s -i -X OPTIONS $B/api/v1/sites/$SITE/subscriptions \
      -H "Origin: $ORIGIN" -H "Access-Control-Request-Method: POST" | grep -i access-control
    # Access-Control-Allow-Origin: https://www.example.com ...

    # unsubscribe the fake browser (else the next sendings try it: the push service answers 404 and it is removed)
    curl -s -X DELETE $B/api/v1/sites/$SITE/subscriptions \
      -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
      -d '{"endpoint": "https://fcm.googleapis.com/fcm/send/curl-test"}'
    # {"unsubscribed":true}

**API with the token** (used by the CMS of the sites)

    # check the token
    curl -s $B/api/v1/ping -H "Authorization: Bearer $TOKEN"
    # {"ok":true,"token":"curl"}

    # queue a message
    curl -s -X POST $B/api/v1/sites/$SITE/messages \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"title": "Hello from curl", "url": "https://www.example.com/?from=curl",
           "options": {"body": "First notification", "tag": "curl-1",
                       "actions": [{"action": "a1", "title": "Open"}]}}'
    # {"id":1,"status":"queued"}   (HTTP 202)

    # a message not before a date (ISO 8601, with or without offset)
    curl -s -X POST $B/api/v1/sites/$SITE/messages \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"title": "Tonight", "send_after": "2030-01-01T20:00:00+01:00"}'

    # status, deliveries and clicks of a message
    curl -s $B/api/v1/sites/$SITE/messages/1 -H "Authorization: Bearer $TOKEN"
    # {"id":1,"status":"queued","deliveries":[],"clicks":0,...}

    # cancel a queued message
    curl -s -X DELETE $B/api/v1/sites/$SITE/messages/2 -H "Authorization: Bearer $TOKEN"
    # {"id":2,"status":"cancelled",...}   (HTTP 409 if it is no longer queued)

**Errors of the API**

    curl -s -X POST $B/api/v1/sites/$SITE/messages -H "Content-Type: application/json" -d '{"title": "x"}'
    # {"error":"Missing bearer token"}   (HTTP 401)

    curl -s -X POST $B/api/v1/sites/$SITE/messages -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" -d '{"title": "x", "options": {"body": 5}}'
    # {"error":"options.body must be a string"}   (HTTP 400)

    curl -s -X POST $B/api/v1/sites/other/messages -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" -d '{"title": "x"}'
    # {"error":"Unknown site"} (HTTP 404), or HTTP 403 for an existing site that is not the one of the token

Repeated requests with a wrong token block the ip (page **IP blacklist**), as the failed logins: in local unblock
`127.0.0.1` there (Remove).

**Old endpoints** (only with `DEFAULT_SITE=<code>` in `.env`)

    curl -s $B/subscription/
    # {"public_key":"..."}
    curl -s -X POST $B/push_message/ -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"title": "Old endpoint", "options": {"body": "b", "data": {"url": "https://www.example.com"}}}'
    # {"id":3,"status":"queued"}

Troubleshooting
===============

The **Test page** of a site (Sites > the site > Test page) shows each step: run **1. Diagnose**, then
**2. Send a test notification**, and read the lines in red with their `→`. The page **Subscribers** shows the last
answer of the push service of every subscriber (red text, also on the counter of the failures), the page **Errors**
the failed sendings.

| What you see | Cause | What to do |
|---|---|---|
| state *push not supported* | page not on https or localhost | open the console / site on https, or on `localhost` / `127.0.0.1` |
| *origin of the page is not PUBLIC_URL*, `POST /subscriptions 403 Origin not allowed` | the page is not on an origin of the site | test page: open the console at `PUBLIC_URL`; real site: add its origin to *Other allowed origins* |
| `... failed (Failed to fetch)` | application not reachable, or CORS | check `curl <PUBLIC_URL>/health`; the origin of the page among the origins of the site (*Integration*) |
| `GET /vapid-public-key 404` | site disabled or wrong code | Sites > the site: *Enabled*, code of `data-site` |
| `GET /vapid-public-key 503` | site without keys | Sites > the site > Keys > Generate |
| *permission: denied* | the visitor (you) blocked the notifications | lock icon in the address bar > Notifications > Allow, reload |
| *service worker ... not registered* | `webpush-sw.js` missing or wrong type | real site: the file at the root, served as JavaScript |
| *pushManager.subscribe failed* | push service of the browser not reachable | network / proxy; Brave: *Use Google services for push messaging* in the settings |
| *subscription made with another VAPID key* | the keys of the site changed | Disable and Enable notifications again |
| *subscription unknown to the server* / *removed* | the POST failed, or removed after failures / 404 | Disable and Enable notifications again |
| push service `403` | VAPID key different from the subscription | as above: subscribe again |
| push service `401` / `400` | VAPID signature or request refused | check the keys and the contact (`mailto:...`) of the site |
| push service `404` / `410` | subscription expired or revoked | subscribe again (the server removes it) |
| push service `413` | message too long | shorter title and options |
| push service `429` | too many requests | retry later; lower `PUSH_WORKERS` |
| push service not reached (timeout, DNS) | the server can't reach the push service | network / firewall of the server toward `fcm.googleapis.com`, `updates.push.services.mozilla.com`, `*.push.apple.com` |
| `201` but no *push received* | browser offline or sleeping, page not controlled by the service worker | reload the page and retry; the browser must be running |
| *push received*, *notification shown*, but nothing visible | the system hides it | Do not disturb / Focus off; notifications of the browser allowed in the system settings |
| *notification NOT shown* | permission revoked meanwhile | allow the notifications of the site again |
| nothing sent at the time of a slot | empty queue, no subscribers, no keys or site disabled | **Home > Today** (yellow: nothing sent), `log/notifier.log` |
| `IP blocked` / `Forbidden :(` | too many failed logins or wrong tokens | page **IP blacklist** (or the SQL in *Operations*) |

Upgrade from the single site version
====================================

The database of the single site version (tables `messages`, `subscribers`, `schedules`, `log_messages`) is moved to the
multi site tables with its site, by `sql/upgrade_2026-10-12_multisite.sql`: the subscribers and the queue are kept.
It replaces step 3 (*Tables*) of *Server installation*; the other steps are the same. On the server, in the shell of
the user `webpush` (`sudo -iu webpush`; the account `webpush` of MariaDB connects without password, *Server setup*,
step 3), after steps 1 and 2 of *Server installation*:

1. Stop the old application (`./stop.sh` in the directory of the old version) and back up the database in
   `/srv/webpush`:

       mariadb-dump webpush > /srv/webpush/webpush-backup-$(date +%F).sql
       ls -lh /srv/webpush/webpush-backup-*.sql

2. Site of the old data: edit the values at the top of the script (code, name, url, contact), and in `.env`
   `DEFAULT_SITE=<the same code>` (the old endpoints `/subscription/` and `/push_message/` keep working):

       nano /srv/webpush/app/sql/upgrade_2026-10-12_multisite.sql
       nano /srv/webpush/app/.env

3. New tables and move of the data:

       cd /srv/webpush/app
       mariadb webpush < sql/create_tables.sql
       mariadb webpush < sql/upgrade_2026-10-12_multisite.sql

   The last query shows the counts of the old and the new tables. Duplicated subscriptions are merged (the most
   recent keys), the queue keeps its order, the old tables are renamed `*_old` (drop them when everything works).

4. Start (step 4 of *Server installation*):

       /srv/webpush/app/docker-start.sh

5. Import the VAPID keys of the old installation, so that the browsers already subscribed keep working: console
   **Sites > the site > Keys > Import** with the content of `private_key.txt` and `public_key.txt` of the old
   version, or in the container (`/srv/webpush/app/log` is mounted in `/app/log`; `<old>` is the directory of the
   old version):

       cp <old>/private_key.txt <old>/public_key.txt /srv/webpush/app/log/
       docker exec webpush python vapid.py import <code> log/private_key.txt log/public_key.txt
       rm /srv/webpush/app/log/private_key.txt /srv/webpush/app/log/public_key.txt

6. `POST /push_message/` now needs an API token (`Authorization: Bearer ...`, page **API tokens**): update the CMS.
   `GET/POST /subscription/` work as before.
7. Move the site to the new client when possible (*Integration in a site*): `webpush-sw.js` replaces the old service
   worker (same scope, the subscriptions are kept) and adds click tracking and the renewal of the subscriptions.

Back: stop the container (`/srv/webpush/app/docker-stop.sh`), restore the backup on an empty database and start the
old version of the code:

    mariadb -e "DROP DATABASE webpush; CREATE DATABASE webpush DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;"
    mariadb webpush < /srv/webpush/webpush-backup-YYYY-MM-DD.sql

In local the same steps run in `~/Projects/webpush`, with `sudo mariadb` instead of `mariadb`.

Updates
=======

Update an installation to the last version of the repository, as its owner (on the server, `sudo -iu webpush`
first):

    cd /srv/webpush/app                                # in local: cd ~/Projects/webpush
    old=$(git rev-parse HEAD)
    git pull --ff-only                                 # or gh repo sync
    git diff --name-only "$old" HEAD -- sql/upgrade_   # database upgrades to run, if any

* Database: every change of the schema comes with a script `sql/upgrade_YYYY-MM-DD_<name>.sql` (the same change is
  also made in `sql/create_tables.sql`). Stop the container first (the old code would run on the new schema, e.g. a
  slot of the schedule), then run the new ones once, in date order, after a backup:

      ./docker-stop.sh
      mariadb-dump webpush > /srv/webpush/webpush-backup-$(date +%F).sql
      mariadb webpush < sql/upgrade_YYYY-MM-DD_<name>.sql

* Application: rebuild the image and recreate the container (the dependencies are reinstalled when
  `requirements.txt` changed). A change of `.env` too needs it: Docker reads it only when the container is created.

      ./docker-start.sh

At the stop of the container the sendings running are completed (within 60 seconds), the scheduler stops; a sending
cut by the stop ends as *failed*.

Sending
=======

Every site has a weekly schedule (page **Schedules**, table `schedule_slot`): at every slot the **next message of the
queue** is sent to **all** the active subscribers. A subscriber receives at most one notification per slot, so the
schedule has a few slots at the best times of the day; the default, created with every site, is every day:

| 07:30 | 12:30 | 15:30 | 18:30 | 21:00 |
|---|---|---|---|---|
| breakfast and commute | lunch break | afternoon | way home | after dinner |

The queue is fed with few messages a day, e.g. the top news of the site (NewsPostX: the posts with the tag `topnews`
of scrapynews, 3-5 a day): a news published at 23:00 leaves at 07:30, more messages than slots wait for the next slots.
The schedule can't be empty (to stop the sendings disable the site); a day without times sends nothing.

* Order of the queue: page **Messages** (arrows); a message with *Send after* waits for its time.
* A slot with an empty queue, without subscribers or without keys sends nothing (yellow in **Home > Today**).
* **Send now**: a message to all the subscribers of the site, at once. **Send test**: to the test browsers of the site
  only (Sites > Integration > *Use this browser as test browser*), the message stays in the queue.
* Outcome of each subscriber: delivered; 404 / 410 subscription removed (`gone`); other errors counted, the subscriber
  is removed after `PUSH_MAX_FAILURES` failures in a row. A removed browser that subscribes again is active again.
* A sending interrupted by a stop of the application ends as *failed* at the next start.
* A disabled site sends nothing and its public API answers 404.

REST API
========

JSON, base `<PUBLIC_URL>/api/v1`. Errors: `{"error": "..."}` with the HTTP status (400 invalid request, 401 token
missing or wrong, 403 token of another site or origin not allowed, 404 unknown site or message, 409 conflict).

| | | |
|---|---|---|
| `GET /sites/<code>/vapid-public-key` | public | applicationServerKey of the site |
| `POST /sites/<code>/subscriptions` | public, origins of the site | `{"subscription": PushSubscription.toJSON()}` |
| `DELETE /sites/<code>/subscriptions` | public, origins of the site | `{"endpoint": "..."}` |
| `POST /sites/<code>/clicks` | public, origins of the site | `{"message_id": 1}` (service worker) |
| `POST /sites/<code>/messages` | token | queue a message, answers `{"id": 1, "status": "queued"}` |
| `GET /sites/<code>/messages/<id>` | token | status, deliveries, clicks |
| `DELETE /sites/<code>/messages/<id>` | token | cancel a queued message |
| `GET /ping` | token | check the token |
| `GET /health` (no `/api/v1`) | public | application and database answer |

Token: `Authorization: Bearer wp_...`, created in the page **API tokens** for one site: it works only for that site.

Body of `POST /sites/<code>/messages`:

| Field | | |
|---|---|---|
| `title` | required | max 255 characters |
| `url` | optional | page opened by a click (shortcut of `options.data.url`) |
| `options` | optional | [options of a notification](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/showNotification#options): body, icon, image, badge, dir, lang, tag, timestamp, requireInteraction, renotify, silent, vibrate, actions (max 2, `{"action", "title"}`), data |
| `send_after` | optional | ISO 8601 date and time: not sent before |

Title and options together: at most 3500 bytes. The links of the buttons: `options.data.actions = {"<action>": "<url>"}`.
The icon and the badge of the site are used when the message has none.

Integration in a site
=====================

Console **Sites > the site > Integration** has the code with the values of the site:

1. `webpush-sw.js` (download from the page, it is `static/webpush-sw.js`) at the root of the site:
   `https://www.example.com/webpush-sw.js`.
2. In the pages (e.g. footer of the theme):

       <script src="https://www.example.com/notify/static/webpush-client.js" defer
               data-api="https://www.example.com/notify/api/v1" data-site="news"
               data-service-worker="/webpush-sw.js"></script>
       <button type="button" data-webpush-toggle hidden data-label-on="Disable notifications">Enable notifications</button>

   The button appears where push is supported and not blocked and switches the subscription. From your own code:
   `WebPush.subscribe()`, `WebPush.unsubscribe()`, `WebPush.state()` (`subscribed`, `unsubscribed`, `blocked`,
   `unsupported`), the event `webpush:change` on `document`.
3. The origin of the pages must be the url of the site or one of its *other allowed origins*
   (e.g. `https://example.com` and `https://www.example.com`). The origin of the console (`PUBLIC_URL`) is always allowed:
   its **Test page** of every site (Sites > the site > Test page) subscribes like the site.
4. The CMS queues the messages with a token (*REST API*).

The service worker can also be served by the CMS instead of a file: a WordPress theme can answer `/webpush-sw.js` from
PHP (with `Content-Type: application/javascript`). A complete integration (Theme Options, bell in the header, link in
the footer, invitation banner, service worker served by WordPress): theme `npx_vibe` of newspostx, `inc/webpush.php`.

Operations
==========

On the server in the shell of the user `webpush` (`sudo -iu webpush`, then `cd /srv/webpush/app`; in local in
`~/Projects/webpush`):

    docker logs -f webpush                  # container output (dependency install at the first start)
    tail -f log/app.log                     # application; also log/notifier.log, log/scheduler.log, log/api.log
    docker ps --filter name=webpush         # state: (healthy)
    docker stats webpush                    # CPU / memory
    docker restart webpush                  # restart, same .env
    ./docker-start.sh                       # new container: after a change of .env or of the code
    ./docker-stop.sh                        # stop: it stays stopped, also after a reboot
    curl http://127.0.0.1:8090/health       # 8080 on the server

* The errors are also in the page **Errors** (kept `ERROR_LOG_RETENTION_DAYS` days).
* Locked out by the ip blacklist (3 failed logins: 60 minutes):
  `mariadb webpush -e "DELETE FROM ip_blacklist WHERE remote_address = '<ip>'"`
* Forgotten password: `mariadb webpush -e "DELETE FROM user"`, the next access opens **Setup** again.
* Backup: `mariadb-dump webpush > /srv/webpush/webpush-$(date +%F).sql`

(as the user `webpush` on the server; in local `sudo mariadb`, `sudo mariadb-dump`)

VAPID keys
==========

Every site has its key pair: the browsers subscribe with the public key, the sendings are signed with the private one.
Generated in the console (Sites > Keys > Generate, or when the site is created). New keys make every subscription of
the site stop working. Keys made with openssl (the old README) are imported as they are:

    openssl ecparam -name prime256v1 -genkey -noout -out vapid_private.pem
    openssl ec -in vapid_private.pem -outform DER | tail -c +8 | head -c 32 | base64 | tr -d '=' | tr '/+' '_-' > private_key.txt
    openssl ec -in vapid_private.pem -pubout -outform DER | tail -c 65 | base64 -w0 | tr -d '=' | tr '/+' '_-' > public_key.txt

Development
===========

The automatic tests don't use the database nor Docker; they run in a separate venv `.venv` (the `venv/` of the
project belongs to the container):

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
    .venv/bin/python -m pytest tests            # about 55 tests, all passed

To debug the application outside of Docker (Flask server with the debugger, only in local): stop the container
(`./docker-stop.sh`), then `WEB_DEBUG=true .venv/bin/python app.py`.

| | |
|---|---|
| `app.py` | console (pages, login), error handlers, start and stop |
| `api.py` | REST API and old endpoints |
| `notifier.py` | sendings: payload, the slots to all the subscribers, outcome of the subscribers |
| `scheduler.py` | slots of the sites, cleanup of ip blacklist and errors |
| `db.py`, `dbpool.py` | queries, connection pool |
| `vapid.py` | VAPID keys (generation, import) |
| `timeline.py` | today's view of the Home |
| `errorlog.py` | errors saved in the table `error_log` |
| `util.py` | ip blacklist, pagination |
| `settings.py` | settings from `.env` |
| `templates/`, `static/main.css` | console (Bootstrap 5, as scrapynews) |
| `static/webpush-client.js`, `static/webpush-sw.js` | code for the sites (also used by the test page of the console) |
| `Dockerfile`, `install_deps.sh`, `docker-start.sh`, `docker-stop.sh` | container |
| `sql/` | tables (`create_tables.sql`), upgrades of the database (`upgrade_YYYY-MM-DD_<name>.sql`) |
| `tests/` | pytest |
