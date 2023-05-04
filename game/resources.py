import logging
import time
import re
from core.extractors import Extractor
from bs4 import BeautifulSoup as Soup
import random


class ResourceManager:
    actual = {}

    requested = {}

    last_notify = {"wood": {"time": 0, "amount": 0},
                   "iron": {"time": 0, "amount": 0},
                   "stone": {"time": 0, "amount": 0}
                  }

    continent = None
    storage = 0
    ratio = 2.5
    max_trade_amount = 4000
    logger = None
    # not allowed to bias
    trade_bias = 1
    last_trade = 0
    trade_max_per_hour = 1
    trade_max_duration = 2
    wrapper = None
    village_id = None
    do_premium_trade = False
    resources_kept_safe = {}
    resources_on_market = {}
    traded_resources = False

    def __init__(self, wrapper=None, village_id=None):
        self.wrapper = wrapper
        self.village_id = village_id

    def update(self, game_state):
        self.actual["wood"] = game_state["village"]["wood"]
        self.actual["stone"] = game_state["village"]["stone"]
        self.actual["iron"] = game_state["village"]["iron"]
        self.actual["pop"] = (
            game_state["village"]["pop_max"] - game_state["village"]["pop"]
        )
        self.storage = game_state["village"]["storage_max"]
        self.check_state()
        self.continent = Extractor.continent(game_state["village"]["display_name"])
        self.logger = logging.getLogger(f'Resource Manager: {game_state["village"]["name"]}')

    def update_notify_resource(self, resource, amount):
        timestamp = int(time.time())
        self.last_notify[resource]["time"] = timestamp
        self.last_notify[resource]["amount"] = amount

    def any_resource_full(self):
        for res in ["wood", "stone", "iron"]:
            if self.actual[res] == self.storage:
                return True
        return False

    def manage_full_resource(self):
        if self.any_resource_full():
            self.logger.info(
                "Village storage is full! Trying to add resource to the market for safe keeping."
            )
            for res in ["wood", "stone", "iron"]:
                if self.actual[res] == self.storage:
                    counter = ["stone", "wood", "iron"]
                    counter.remove(res)
                    c = random.choice(counter)
                    self.logger.info(f"Adding {res} for {c} to market for safe keeping")
                    if self.trade(res, 1000, c, 1000, False):
                        self.actual[res] -= 1000
                        if res in self.resources_kept_safe:
                            self.resources_kept_safe[res] += 1000
                        else:
                            self.resources_kept_safe[res] = 1000
        elif self.resources_kept_safe != {}:
            self.logger.info(
                "Kept resources safe, check if we have enough storage to get them back."
            )
            all_good = True
            for res in self.resources_kept_safe:
                if self.actual[res] + self.resources_kept_safe[res] >= self.storage:
                    all_good = False
                    break
            if all_good:
                self.logger.info(
                    "Have enough storage to remove all resources from the market!"
                )
                self.drop_existing_trades()
                self.resources_kept_safe = {}

    def check_premium_price(self):
        url = f"game.php?village={self.village_id}&screen=market&mode=exchange"
        res = self.wrapper.get_url(url=url)
        data = Extractor.premium_data(res.text)
        avg_exchange_rate = Extractor.premium_exchange_rate(res.text)
        if not data or "stock" not in data:
            self.logger.warning("Error reading premium data!")
            return False
        price_fetch = ["wood", "stone", "iron"]
        prices = {}
        real_rate = {}
        now = int(time.time())
        for p in price_fetch:
            prices[p] = data["stock"][p] * data["rates"][p]
            real_rate[p] = 1 / data['rates'][p] / (data["tax"]["buy"] + 1)
            # if the current exchange rate is 35% below the average
            if real_rate[p] < avg_exchange_rate[p] * 0.65:
                # use the notification if current rate is better then than the previous one
                # or more than 60 minutes have passed since the previous one (antyspam)
                if (
                    self.last_notify[p]["amount"] > real_rate[p]
                    or self.last_notify[p]["time"] + 3600 < now
                ):
                    self.update_notify_resource(p, real_rate[p])
                    self.wrapper.discord_notifier.send(f"Resource {p} has a good sell ratio in {self.continent} (1:{int(real_rate[p])})")
            elif real_rate[p] > avg_exchange_rate[p] * 1.45:
                # use the notification if current rate is better then than the previous one
                # or more than 60 minutes have passed since the previous one (antyspam)
                if (
                    self.last_notify[p]["amount"] < real_rate[p]
                    or self.last_notify[p]["time"] + 3600 < now
                ):
                    self.update_notify_resource(p, real_rate[p])
                    self.wrapper.discord_notifier.send(f"Resource {p} has a good buy ratio in {self.continent} (1:{int(real_rate[p])})")

        return prices

    def do_premium_stuff(self):
        gpl = self.get_plenty_off()
        prices = self.check_premium_price()
        self.logger.debug(f"Trying premium trade: gpl {gpl} do? {self.do_premium_trade}")
        if gpl and self.do_premium_trade and prices:
            self.logger.info(f"Actual premium prices: {prices}")

            if gpl in prices and prices[gpl] * 1.1 < self.actual[gpl]:
                self.logger.info(
                    "Attempting trade of %d %s for premium point" % (prices[gpl], gpl)
                )
                res = self.wrapper.get_api_action(self.village_id, action="exchange_begin", params={"screen": "market"}, data={f"sell_{gpl}": "1"})

                rate_hash, amount, mb = Extractor.premium_data_confirm(res)
                self.wrapper.get_api_action(self.village_id, action="exchange_confirm", params={"screen": "market"}, data={f"sell_{gpl}": f"{amount}", f"rate_{gpl}": f"{rate_hash}", "mb": f"{mb}"})

    def check_state(self):
        for source in self.requested:
            if source == "snob":
                continue
            for res in self.requested[source]:
                if self.requested[source][res] <= self.actual[res]:
                    self.requested[source][res] = 0

    def request(self, source="building", resource="wood", amount=1):
        if source in self.requested:
            self.requested[source][resource] = amount
        else:
            self.requested[source] = {resource: amount}

    def can_recruit(self):
        if self.actual["pop"] == 0:
            self.logger.info("Can't recruit, no room for pops!")
            to_remove = []
            for x in self.requested:
                if "recruitment" in x:
                    to_remove.append(x)
            for x in to_remove:
                del self.requested[x]
            return False

        for x in self.requested:
            if "recruitment" in x:
                continue
            types = self.requested[x]
            for sub in types:
                if types[sub] > 0:
                    return False
        return True

    def get_plenty_off(self):
        most_of = 0
        most = None
        for sub in self.actual:
            f = 1
            for sr in self.requested:
                # if resources is needed for feaure (requested) building > continue
                if sub in self.requested[sr] and self.requested[sr][sub] > 0:
                    f = 0
            if not f:
                continue
            if sub == "pop":
                continue
            # self.logger.debug(f"We have {self.actual[sub]} {sub}. Enough? {self.actual[sub]} > {int(self.storage / self.ratio)}")
            # if more than 40% (ratio 2.5) of the storage
            if self.actual[sub] > int(self.storage / self.ratio) and self.actual[sub] > most_of:
                most = sub
                most_of = self.actual[sub]
        if most:
            self.logger.debug(f"We have plenty of {most}")

        return most

    def in_need_of(self, obj_type):
        for x in self.requested:
            types = self.requested[x]
            if obj_type in types and self.requested[x][obj_type] > 0:
                return True
        return False

    def in_need_amount(self, obj_type):
        amount = 0
        for x in self.requested:
            types = self.requested[x]
            if obj_type in types and self.requested[x][obj_type] > 0:
                amount += self.requested[x][obj_type]
        return amount

    def get_needs(self):
        needed_the_most = None
        needed_amount = 0
        for x in self.requested:
            types = self.requested[x]
            for obj_type in types:
                if (
                    self.requested[x][obj_type] > 0
                    and self.requested[x][obj_type] > needed_amount
                ):
                    needed_amount = self.requested[x][obj_type]
                    needed_the_most = obj_type
        if needed_the_most:
            return needed_the_most, needed_amount
        return None

    def trade(self, me_item, me_amount, get_item, get_amount, set_trade_time=True):
        url = f"game.php?village={self.village_id}&screen=market&mode=own_offer"
        res = self.wrapper.get_url(url=url)
        if 'market_merchant_available_count">0' in res.text:
            self.logger.debug("Not trading because not enough merchants available")
            return False
        payload = {
            "res_sell": me_item,
            "sell": me_amount,
            "res_buy": get_item,
            "buy": get_amount,
            "max_time": self.trade_max_duration,
            "multi": 1,
            "h": self.wrapper.last_h,
        }
        post_url = f"game.php?village={self.village_id}&screen=market&mode=own_offer&action=new_offer"

        self.wrapper.post_url(post_url, data=payload)
        if set_trade_time:
            self.traded_resources = True
            self.last_trade = int(time.time())
        if not me_item in self.resources_on_market:
            self.resources_on_market[me_item] = me_amount
        else:
            self.resources_on_market[me_item] += me_amount
        return True

    def drop_existing_trades(self):
        self.traded_resources = False
        url = f"game.php?village={self.village_id}&screen=market&mode=all_own_offer"
        data = self.wrapper.get_url(url)
        existing = re.findall(r'data-id="(\d+)".+?data-village="(\d+)"', data.text)
        for entry in existing:
            offer, village = entry
            if village == str(self.village_id):
                post_url = f"game.php?village={self.village_id}&screen=market&mode=all_own_offer&action=delete_offers"

                post = {f"id_{offer}": "on", "delete": "Verwijderen", "h": self.wrapper.last_h}
                self.wrapper.post_url(url=post_url, data=post)
                self.logger.info(f"Removing offer {offer} from market because it existed too long")
        self.resources_on_market = {}
    def readable_ts(self, seconds):
        seconds -= int(time.time())
        seconds %= 24 * 3600
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60

        return "%d:%02d:%02d" % (hour, minutes, seconds)

    def manage_market(self, drop_existing=True):
        # Try to 'safe' resources on the market if nessesary
        self.manage_full_resource()
        need_to_drop = False
        for x in self.requested:
            if x in self.resources_on_market:
                self.logger.debug(
                    f"We need {x} and we are currently offering it on the market."
                )
                need_to_drop = True

        if need_to_drop:
            self.drop_existing_trades()
            # No need to do the market any further
            return

        last = self.last_trade + int(3600 * self.trade_max_per_hour)
        if last > int(time.time()):
            self.logger.debug("Won't trade for %s" % (self.readable_ts(last)))
            return

        get_h = time.localtime().tm_hour
        if get_h in range(6) or get_h == 23:
            self.logger.debug("Not managing trades between 23h-6h")
            return
        if self.traded_resources and drop_existing and self.resources_kept_safe == {}:
            self.drop_existing_trades()

        plenty = self.get_plenty_off()
        if plenty and not self.in_need_of(plenty):
            need = self.get_needs()
            if need :
                # check incoming resources
                url = (
                    "game.php?village=%s&screen=market&mode=other_offer"
                    % self.village_id
                )
                res = self.wrapper.get_url(url=url)
                p = re.compile(
                    r"Aankomend:\s.+\"icon header (.+?)\".+?<\/span>(.+) ", re.M
                )
                incoming = p.findall(res.text)
                resource_incoming = {}
                if incoming:
                    resource_incoming[incoming[0][0].strip()] = int(
                        "".join([s for s in incoming[0][1] if s.isdigit()])
                    )
                    self.logger.info(
                        f"There are resources incoming! {resource_incoming}"
)
                item, how_many = need
                how_many = round(how_many, -1)
                if item in resource_incoming and resource_incoming[item] >= how_many:
                    self.logger.info(
                        f"Needed {item} already incoming! ({resource_incoming[item]} >= {how_many})"
                    )
                    return
                if how_many < 250:
                    return

                self.logger.debug("Checking current market offers")
                how_many -= self.check_other_offers(item, how_many, plenty)
                if how_many < 0:
                    self.logger(f"Traded enough!")
                    return

                if how_many > self.max_trade_amount:
                    how_many = self.max_trade_amount
                    self.logger.debug(
                        "Lowering trade amount of %d to %d because of limitation"
                        % (how_many, self.max_trade_amount)
                    )
                biased = int(how_many * self.trade_bias)
                if self.actual[plenty] < biased:
                    self.logger.debug("Cannot trade because insufficient resources")
                    return
                self.logger.info(
                    "Adding market trade of %d %s -> %d %s"
                    % (how_many, item, biased, plenty)
                )
                self.wrapper.reporter.report(
                    self.village_id,
                    "TWB_MARKET",
                    "Adding market trade of %d %s -> %d %s"
                    % (how_many, item, biased, plenty),
                )

                self.trade(plenty, biased, item, how_many)

    def get_incoming_resources(self, res):
        soup = Soup(res, features="html.parser")
        inc = soup.select_one(
            "#market_status_bar table.vis:nth-of-type(2) th:nth-of-type(1)"
        )
        p = re.compile(r"\"icon header (.+?)\".+?<\/span>(.+?) <", re.S | re.M)
        incoming = p.findall(str(inc))
        resource_incoming = {}
        for resource, amount_str in incoming:
            try:
                amount = int("".join([s for s in amount_str if s.isdigit()]))
                resource_incoming[resource] = amount
            except:
                self.logger.warning(
                    f"Unable to parse incoming resources! {resource} {amount_str}"
                )
                continue

        return resource_incoming

    def check_other_offers(self, item, how_many, sell):
        willing_to_sell = self.actual[sell] - self.in_need_amount(sell) - 500
        # Always keep at least 500 in the bank
        if willing_to_sell < 0:
            self.logger.debug(
                f"Not willing to sell {sell}. I have {self.actual[sell]} {sell}."
            )
            return False

        url = f"game.php?village={self.village_id}&screen=market&mode=other_offer"
        res = self.wrapper.get_url(url=url)
        p = re.compile(
            r"(?:<!-- insert the offer -->\n+)\s+<tr>(.*?)<\/tr>", re.S | re.M
        )
        cur_off_tds = p.findall(res.text)
        resource_incoming = self.get_incoming_resources(res.text)
        if resource_incoming != {}:
            self.logger.debug(f"Resource(s) incoming: {resource_incoming}")
        if item in resource_incoming:
            how_many = how_many - resource_incoming[item]
            if how_many < 1:
                self.logger.info("Requested resource already incoming!")
                return False

        self.logger.debug(
            f"Found {len(cur_off_tds)} offers on market, willing to sell {willing_to_sell} {sell}"
        )

        for tds in cur_off_tds:
            res_offer = re.findall(
                r"<span class=\"icon header (.+?)\".+?>(.+?)</td>", tds
            )
            off_id = re.findall(
                r"<input type=\"hidden\" name=\"id\" value=\"(\d+)", tds
            )

            if len(off_id) < 1:
                # Not enough resources to trade
                continue

            offer = self.parse_res_offer(res_offer, off_id[0])
            if (
                offer["offered"] == item
                and offer["offer_amount"] >= how_many
                and offer["wanted"] == sell
                and offer["wanted_amount"] <= willing_to_sell
            ):
                self.logger.info(
                    f"Good offer: {offer['offer_amount']} {offer['offered']} for {offer['wanted_amount']} {offer['wanted']}"
                )
                # Take the deal!
                payload = {
                    "count": 1,
                    "id": offer["id"],
                    "h": self.wrapper.last_h,
                }
                post_url = f"game.php?village={self.village_id}&screen=market&mode=other_offer&action=accept_multi&start=0&id={offer['id']}&h={self.wrapper.last_h}"
                # print(f"Would post: {post_url} {payload}")
                self.wrapper.post_url(post_url, data=payload)
                self.actual[offer["wanted"]] = (
                    self.actual[offer["wanted"]] - offer["wanted_amount"]
                )
                return offer["offer_amount"]
            if (
                offer["offered"] == item
                and offer["wanted"] == sell
                and offer["wanted_amount"] <= willing_to_sell
            ):
                self.logger.info(
                    f"Decent offer: {offer['offer_amount']} {offer['offered']} for {offer['wanted_amount']} {offer['wanted']}"
                )
                # Take the deal!
                payload = {
                    "count": 1,
                    "id": offer["id"],
                    "h": self.wrapper.last_h,
                }
                post_url = f"game.php?village={self.village_id}&screen=market&mode=other_offer&action=accept_multi&start=0&id={offer['id']}&h={self.wrapper.last_h}"
                # print(f"Would post: {post_url} {payload}")
                self.wrapper.post_url(post_url, data=payload)
                self.actual[offer["wanted"]] = (
                    self.actual[offer["wanted"]] - offer["wanted_amount"]
                )
                return offer["offer_amount"]

        # No useful offers found
        return 0

    def parse_res_offer(self, res_offer, id):
        off, want, ratio = res_offer
        res_offer, res_offer_amount = off
        res_wanted, res_wanted_amount = want

        return {
            "id": id,
            "offered": res_offer,
            "offer_amount": int("".join([s for s in res_offer_amount if s.isdigit()])),
            "wanted": res_wanted,
            "wanted_amount": int(
                "".join([s for s in res_wanted_amount if s.isdigit()])
            ),
        }
