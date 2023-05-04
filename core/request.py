import requests

try:
    from urllib.parse import urljoin, urlencode
except ImportError:
    from urlparse import urljoin, urlencode
import logging
import re
import time
import random
import json
import os
from core.reporter import ReporterObject
from core.notifier import DiscordNotifier


class WebWrapper:
    web = None
    headers = {
        "user-agent": "Mozilla/5.0 (Windows NT 6.3; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.97 Safari/537.36",
        "upgrade-insecure-requests": "1",
    }
    endpoint = None
    logger = logging.getLogger("Requests")
    server = None
    last_response = None
    last_h = None
    priority_mode = False
    auth_endpoint = None
    reporter = None
    delay = 1.0
    discord = None
    discord_notifier = None
    proxy = {}

    def __init__(self, url, server=None, endpoint=None, reporter_enabled=False, reporter_constr=None, discord=None, discord_endpoint=None, discord_notifier=None, discord_notifier_endpoint=None, proxy_enabled=False, proxy_endpoint=None):
        self.web = requests.session()
        if proxy_enabled and proxy_endpoint:
            self.proxy['http'] = proxy_endpoint
            self.proxy['https'] = proxy_endpoint
            self.web.proxies = self.proxy
            self.test_proxy_connection()

        self.auth_endpoint = url
        self.server = server
        self.endpoint = endpoint
        self.reporter = ReporterObject(
            enabled=reporter_enabled, connection_string=reporter_constr
        )
        self.discord = DiscordNotifier(discord=discord, discord_endpoint=discord_endpoint)
        self.discord_notifier = DiscordNotifier(discord=discord_notifier, discord_endpoint=discord_notifier_endpoint)

    def test_proxy_connection(self):
        res = self.web.get(r'http://jsonip.com')
        ip = res.json()['ip']
        self.logger.info("Proxy checker. Your current ip - %s" % ip)
        input("If IP is correct press any key to continue...")

    def post_process(self, response):
        xsrf = re.search('<meta content="(.+?)" name="csrf-token"', response.text)
        if xsrf:
            self.headers["x-csrf-token"] = xsrf.group(1)
            self.logger.debug("Set CSRF token")
        elif "x-csrf-token" in self.headers:
            del self.headers["x-csrf-token"]
        self.headers["Referer"] = response.url
        self.last_response = response
        get_h = re.search(r"&h=(\w+)", response.text)
        if get_h:
            self.last_h = get_h.group(1)

    def get_url(self, url, headers=None):
        self.headers["Origin"] = (
            self.endpoint if self.endpoint else self.auth_endpoint
        ).rstrip("/")
        if not self.priority_mode:
            time.sleep(random.randint(int(3 * self.delay), int(7 * self.delay)))
        url = urljoin(self.endpoint if self.endpoint else self.auth_endpoint, url)
        if not headers:
            headers = self.headers
        try:
            res = self.web.get(url=url, headers=headers)
            self.logger.debug("GET %s [%d]" % (url, res.status_code))
            self.post_process(res)
            if 'data-bot-protect="forced"' in res.text:
                msg = "Bot protection hit! Cannot continue. Solve captcha and restart"
                self.logger.warning(msg)
                self.discord.send(msg)
                self.reporter.report(
                    0,
                    "TWB_RECAPTCHA",
                    "Stopping bot, press any key once captcha has been solved",
                )
                input("Press any key...")
                return self.get_url(url, headers)
            return res
        except Exception as e:
            self.logger.warning("GET %s: %s" % (url, str(e)))
            return None

    def post_url(self, url, data, headers=None):
        if not self.priority_mode:
            time.sleep(random.randint(int(3 * self.delay), int(7 * self.delay)))
        self.headers["Origin"] = (
            self.endpoint if self.endpoint else self.auth_endpoint
        ).rstrip("/")
        url = urljoin(self.endpoint if self.endpoint else self.auth_endpoint, url)
        enc = urlencode(data)
        if not headers:
            headers = self.headers
        try:
            res = self.web.post(url=url, data=data, headers=headers)
            self.logger.debug("POST %s %s [%d]" % (url, enc, res.status_code))
            self.post_process(res)
            return res
        except Exception as e:
            self.logger.warning("POST %s %s: %s" % (url, enc, str(e)))
            return None

    def start(
        self,
    ):
        if os.path.exists("cache/session.json"):
            with open("cache/session.json") as f:
                session_data = json.load(f)
                self.web.cookies.update(session_data["cookies"])
                get_test = self.get_url("game.php?screen=overview")
                if "game.php" in get_test.url:
                    return True
                else:
                    msg = "Current session cache not valid. Waiting for new cookie."
                    self.logger.warning(msg)
                    self.discord.send(msg)
        self.web.cookies.clear()
        cinp = input("Enter browser cookie string> ")
        cookies = {}
        cinp = cinp.strip()
        for itt in cinp.split(";"):
            itt = itt.strip()
            kvs = itt.split("=")
            k = kvs[0]
            v = "=".join(kvs[1:])
            cookies[k] = v
        self.web.cookies.update(cookies)
        self.logger.info("Game Endpoint: %s" % self.endpoint)

        for c in self.web.cookies:
            cookies[c.name] = c.value

        with open("cache/session.json", "w") as f:
            session = {
                "endpoint": self.endpoint,
                "server": self.server,
                "cookies": cookies,
            }
            json.dump(session, f)

    def get_action(self, village_id, action):
        url = "game.php?village=%s&screen=%s" % (village_id, action)
        response = self.get_url(url)
        return response

    def get_api_data(self, village_id, action, params={}):

        custom = dict(self.headers)
        custom["accept"] = "application/json, text/javascript, */*; q=0.01"
        custom["x-requested-with"] = "XMLHttpRequest"
        custom["tribalwars-ajax"] = "1"
        req = {"ajax": action, "village": village_id, "screen": "api"}
        req.update(params)
        payload = "game.php?%s" % urlencode(req)
        url = urljoin(self.endpoint, payload)
        res = self.get_url(url, headers=custom)
        if res.status_code == 200:
            try:
                return res.json()
            except:
                return res

    def post_api_data(self, village_id, action, params={}, data={}):

        custom = dict(self.headers)
        custom["accept"] = "application/json, text/javascript, */*; q=0.01"
        custom["x-requested-with"] = "XMLHttpRequest"
        custom["tribalwars-ajax"] = "1"
        req = {"ajax": action, "village": village_id, "screen": "api"}
        req.update(params)
        payload = "game.php?%s" % urlencode(req)
        url = urljoin(self.endpoint, payload)
        if "h" not in data:
            data["h"] = self.last_h
        res = self.post_url(url, data=data, headers=custom)
        if res.status_code == 200:
            try:
                return res.json()
            except:
                return res

    def get_api_action(self, village_id, action, params={}, data={}):

        custom = dict(self.headers)
        custom["accept"] = "application/json, text/javascript, */*; q=0.01"
        custom["x-requested-with"] = "XMLHttpRequest"
        custom["tribalwars-ajax"] = "1"
        req = {"ajaxaction": action, "village": village_id, "screen": "api"}
        req.update(params)
        payload = "game.php?%s" % urlencode(req)
        url = urljoin(self.endpoint, payload)
        if "h" not in data:
            data["h"] = self.last_h
        res = self.post_url(url, data=data, headers=custom)
        if res.status_code == 200:
            try:
                return res.json()
            except:
                return res
