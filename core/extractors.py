import re
import json
from datetime import datetime, timedelta
import statistics


class Extractor:
    @staticmethod
    def new_active_building_queue(res):
        if type(res) != str:
            res = res.text
        builder = re.search('(?s)<table id="build_queue"(.+?)</table>', res)
        if builder is None:
            return [], []
        p = re.compile(
            r'<tr class=".+? buildorder_(.+?)"[ >].+?data-available-to="(.+?)"',
            re.M | re.S,
        )
        queued = re.findall(p, builder.group(1))

        current_ts = []
        buildings_q = []
        for building, timestr in queued:
            buildings_q.append(building)
            current_ts.append(int(timestr))

        return current_ts, buildings_q

    @staticmethod
    def village_data(res):
        if type(res) != str:
            res = res.text
        grabber = re.search(r"var village = (.+);", res)
        if grabber:
            data = grabber[1]
            return json.loads(data, strict=False)

    @staticmethod
    def game_state(res):
        if type(res) != str:
            res = res.text
        grabber = re.search(r"TribalWars\.updateGameData\((.+?)\);", res)
        if grabber:
            data = grabber[1]
            return json.loads(data, strict=False)

    @staticmethod
    def building_data(res):
        if type(res) != str:
            res = res.text
        dre = re.search(r"(?s)BuildingMain.buildings = (\{.+?\});", res)
        if dre:
            return json.loads(dre[1], strict=False)

        return None

    @staticmethod
    def get_quests(res):
        if type(res) != str:
            res = res.text
        get_quests = re.search(r"Quests.setQuestData\((\{.+?\})\);", res)
        if get_quests:
            result = json.loads(get_quests[1], strict=False)
            for quest in result:
                data = result[quest]
                if data["goals_completed"] == data["goals_total"]:
                    return quest
        return None

    @staticmethod
    def get_quest_rewards(res):
        if type(res) != str:
            res = res.text
        get_rewards = re.search(r"RewardSystem\.setRewards\(\s*(\[\{.+?\}\]),", res)
        rewards = []
        if get_rewards:
            result = json.loads(get_rewards[1], strict=False)
            for reward in result:
                if reward["status"] == "unlocked":
                    rewards.append(reward)
        # Return all off them
        return rewards

    @staticmethod
    def get_daily_reward(res):
        if type(res) != str:
            res = res.text
        get_daily = re.search(r'DailyBonus.init\((\s+\{.*\}),', res)
        res = json.loads(get_daily[1])
        reward_count_unlocked = str(res["reward_count_unlocked"])
        if (
            reward_count_unlocked and
            res["chests"][reward_count_unlocked]["is_collected"] == False
        ):
            return reward_count_unlocked
        else:
            return None

    @staticmethod
    def map_data(res):
        if type(res) != str:
            res = res.text
        data = re.search(r"(?s)TWMap.sectorPrefech = (\[(.+?)\]);", res)
        if data:
            return json.loads(data[1], strict=False)

    @staticmethod
    def smith_data(res):
        if type(res) != str:
            res = res.text
        data = re.search(r"(?s)BuildingSmith.techs = (\{.+?\});", res)
        if data:
            return json.loads(data[1], strict=False)
        return None

    @staticmethod
    def premium_data(res):
        if type(res) != str:
            res = res.text
        data = re.search(r"(?s)PremiumExchange.receiveData\((.+?)\);", res)
        if data:
            return json.loads(data[1], strict=False)
        return None

    @staticmethod
    def premium_exchange_rate(res):
        if type(res) != str:
            res = res.text
        data = re.findall(r'data: (\[\[.+\]\]),', res)
        rate = {"wood": [], "stone": [], "iron": []}
        i = 0
        for x in data:
            # convert string output to list
            res = json.loads(x)
            i += 1
            # resources have always a static order
            resource = "wood" if i == 1 else "stone" if i == 2 else "iron"
            temp_rate = []
            for y in res:
                temp_rate.append(float(y[-1]))
            rate[resource] = int(statistics.mean(temp_rate))
        return rate


    @staticmethod
    def premium_data_confirm(res):
        rate_hash = res['response'][0]['rate_hash']
        amount = str(res['response'][0]['amount']).replace("-", "")
        mb = "1"
        return rate_hash, amount, mb

    @staticmethod
    def recruit_data(res):
        if type(res) != str:
            res = res.text
        data = re.search(r"(?s)unit_managers.units = (\{.+?\});", res)
        if data:
            raw = data[1]
            quote_keys_regex = r"([\{\s,])(\w+)(:)"
            processed = re.sub(quote_keys_regex, r'\1"\2"\3', raw)
            return json.loads(processed, strict=False)

    @staticmethod
    def units_in_village(res):
        if type(res) != str:
            res = res.text
        res = re.sub('(?s)<table id="units_home".+?</table>', "", res)
        return re.findall(
            r'(?s)<a href="#" class="unit_link" data-unit="(\w+)".+?(\d+)</strong>', res
        )

    @staticmethod
    def active_building_queue(res):
        if type(res) != str:
            res = res.text
        builder = re.search('(?s)<table id="build_queue"(.+?)</table>', res)
        if not builder:
            return 0

        return builder.group(1).count('<a class="btn btn-cancel"')

    @staticmethod
    def active_recruit_queue(res):
        if type(res) != str:
            res = res.text
        return re.findall(r"(?s)TrainOverview\.cancelOrder\((\d+)\)", res)

    @staticmethod
    def new_active_recruit_queue(res):
        if type(res) != str:
            res = res.text
        builder = re.search('(?s)<div class="trainqueue_wrap"(.+?)<\/tbody>', res)
        p = re.compile(
            r'class="unit_sprite unit_sprite_smaller (.+?)">.+?div>.+?(\d+).+<td class="lit-item">.+? (\d{2}:\d{2}:\d{2})',
            re.M | re.S,
        )
        queued = re.findall(p, builder.group(1))
        previous_time = None
        current_ts = []
        units_q = []
        for unit, amount, timestr in queued:
            now = datetime.now()
            startTime = datetime.strptime(timestr, "%H:%M:%S")
            d = datetime.combine(datetime.today(), startTime.time())
            if previous_time and d < previous_time:
                d = d + timedelta(days=1)
            if d < now:
                d = d + timedelta(days=1)
            previous_time = d
            ts = int(d.timestamp())
            units_q.append(unit)
            current_ts.append(ts)

        return current_ts, units_q

    @staticmethod
    def active_attacks(res):
        if type(res) != str:
            res = res.text
        builder = re.search('(?s)<div id="commands_outgoings"(.+?)<\/tbody>', res)
        if not builder:
            return [], []
        p = re.compile(
            r'data-command-type="(.+?)">.+?data-endtime="(\d+)"', re.M | re.S
        )
        queued = re.findall(p, builder.group(1))
        outgoing = []
        returning = []
        for attack_or_return, timestr in queued:
            if attack_or_return == "return":
                returning.append(int(timestr))
            else:
                outgoing.append(int(timestr))

        return outgoing, returning

    @staticmethod
    def village_ids_from_overview(res):
        if type(res) != str:
            res = res.text
        villages = re.findall(r'<span class="quickedit-vn" data-id="(\d+)"', res)
        return list(set(villages))

    @staticmethod
    def units_in_total(res):
        if type(res) != str:
            res = res.text
        # hide units from other villages
        res = re.sub(r'(?s)<span class="village_anchor.+?</tr>', "", res)
        return re.findall(
            r"(?s)class=\Wunit-item unit-item-([a-z]+)\W.+?(\d+)</td>", res
        )

    @staticmethod
    def attack_form(res):
        if type(res) != str:
            res = res.text
        data = re.findall(r'(?s)<input.+?name="(.+?)".+?value="(.*?)"', res)
        return data

    @staticmethod
    def attack_duration(res):
        if type(res) != str:
            res = res.text
        data = re.search(r'<span class="relative_time" data-duration="(\d+)"', res)
        if data:
            return int(data[1])
        return 0

    @staticmethod
    def report_table(res):
        if type(res) != str:
            res = res.text
        data = re.findall(r'(?s)class="report-link" data-id="(\d+)"', res)
        return data

    @staticmethod
    def continent(res):
        continent = re.search(r'(K\d+)', res)
        if continent:
            return continent[0]