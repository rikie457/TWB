from codecs import decode
from datetime import datetime, timedelta
import logging
import json
import os
import time
import random

from game.buildingmanager import BuildingManager
from game.troopmanager import TroopManager
from game.attack import AttackManager
from game.map import Map
from game.resources import ResourceManager
from game.defence_manager import DefenceManager
from game.reports import ReportManager
from game.snobber import SnobManager

from core.extractors import Extractor
from core.templates import TemplateManager
from core.twplus import TwPlus


class Village:
    village_id = None
    builder = None
    units = None
    wrapper = None
    resources = {}
    game_data = {}
    logger = None
    force_troops = False
    area = None
    snobman = None
    attack = None
    resman = None
    def_man = None
    rep_man = None
    config = None
    village_set_name = None
    next_event = {"kind": None, "time": None}

    twp = TwPlus()

    def __init__(self, village_id=None, wrapper=None):
        self.village_id = village_id
        self.wrapper = wrapper

    def set_next_event(self, kind, time):
        if time == 0:
            # Ignore 0 timers!
            return
        tsdtn = datetime.now().timestamp()
        min_delay = self.get_config("bot", "active_delay")
        if time < tsdtn:
            # Ignore old timers
            return

        if not self.next_event["time"]:
            self.next_event["time"] = time
            self.next_event["kind"] = kind
            self.logger.debug(f"Initial next event: {kind} at {time}")
        else:
            secs = time - tsdtn
            self.logger.debug(
                f"Is {kind} at {time} sooner than {self.next_event['time']}? {self.next_event['time'] > time} Too soon? {secs < min_delay}"
            )
            if self.next_event["time"] > time and secs > min_delay:
                self.next_event["time"] = time
                self.next_event["kind"] = kind

    def get_seconds_till_next_event(self):
        if self.next_event["time"]:
            stne = round(self.next_event["time"] - time.time(), 2)
            self.logger.info(
                "Seconds until next event: {stne} ({self.next_event['kind']})"
            )
            return stne
        return 0

    def determine_next_back(self, res):
        outgoing, returning = Extractor.active_attacks(res)

        if len(returning) > 0:
            tsdtn = datetime.now().timestamp()
            min_delay = self.get_config("bot", "active_delay")
            back = min(returning)
            secs = back - tsdtn
            skipped = 0
            while secs < min_delay:
                returning.remove(back)
                if len(returning) == 0:
                    back = None
                    break
                back = min(returning)
                secs = back - tsdtn
                skipped += 1

            if back is not None:
                first_back = datetime.fromtimestamp(back)
                self.logger.info(
                    "First attack to be back home is {first_back} (Skipped {skipped}) | There are {len(returning) + skipped} coming back and {len(outgoing)} attacks underway."
                )
                self.set_next_event("back", back)
                return
        # There are only outgoing attacks!
        if len(outgoing) > 0:
            first_back = datetime.fromtimestamp(min(outgoing))
            self.logger.info(
                "First attack to land is {first_back} | There are {len(outgoing)} attacks underway."
            )
            self.set_next_event("outgoing", min(outgoing))

    def determine_next_building_done(self):
        if not self.builder:
            return
        if len(self.builder.waits) == 0:
            return
        # Compensate for building quick build
        next_build = datetime.fromtimestamp(min(self.builder.waits))
        next_build -= timedelta(minutes=2)

        self.set_next_event("building", int(next_build.timestamp()))

    def determine_first_gather_back(self):
        if not self.units:
            return
        if self.units.first_gathering_back:
            self.set_next_event("gather", self.units.first_gathering_back)

    def determine_next_recruitment(self):
        if not self.units:
            return
        for building in self.units.wait_for[self.village_id]:
            # Only send this when we are waiting for recruitment!
            if self.units.wait_for[self.village_id][building] > 0:
                self.set_next_event(
                    f"recruit_{building}",
                    self.units.wait_for[self.village_id][building],
                )

    def get_config(self, section, parameter, default=None):
        if section not in self.config:
            self.logger.warning("Configuration section %s does not exist!" % section)
            return default
        if parameter not in self.config[section]:
            self.logger.warning(
                "Configuration parameter %s:%s does not exist!" % (section, parameter)
            )
            return default
        return self.config[section][parameter]

    def get_village_config(self, village_id, parameter, default=None):
        if village_id not in self.config["villages"]:
            return default
        vdata = self.config["villages"][village_id]
        if parameter not in vdata:
            self.logger.warning(
                "Village %s configuration parameter %s does not exist!"
                % (village_id, parameter)
            )
            return default
        return vdata[parameter]

    def setup_builder(self):
        if not self.builder:
            self.builder = BuildingManager(
                wrapper=self.wrapper, village_id=self.village_id
            )
            self.builder.resman = self.resman
            # manage buildings (has to always run because recruit check depends on building levels)
        build_config = self.get_village_config(
            self.village_id, parameter="building", default=None
        )
        if not build_config:
            self.logger.warning(
                "Village %d does not have 'building' config override!" % self.village_id
            )
            build_config = self.get_config(
                section="building", parameter="default", default="purple_predator"
            )
        new_queue = TemplateManager.get_template(
            category="builder", template=build_config
        )
        if not self.builder.raw_template or self.builder.raw_template != new_queue:
            self.builder.queue = new_queue
            self.builder.raw_template = new_queue
            if not self.get_config(
                section="world", parameter="knight_enabled", default=False
            ):
                self.builder.queue = [
                    x for x in self.builder.queue if "statue" not in x
                ]
        self.builder.max_lookahead = self.get_config(
            section="building", parameter="max_lookahead", default=2
        )
        self.builder.max_queue_len = self.get_config(
            section="building", parameter="max_queued_items", default=2
        )
        self.builder.game_state = self.game_data
        self.builder.update_building_levels()

    def run_builder(self):
        if not self.builder:
            self.setup_builder()

        if (
            self.get_village_config(
                self.village_id, parameter="prioritize_snob", default=False
            )
            and self.snobman
            and self.snobman.can_snob
            and self.snobman.is_incomplete
        ):
            self.logger.info("Not building because snob has insufficient funds!")
            return

        if "snob" in self.resman.requested:
            self.logger.info("Not building because snob has insufficient funds!")
        elif "research" in self.resman.requested:
            self.logger.info("Not building because research is needed")
        else:
            self.builder.start_update(
                build=self.get_config(
                    section="building", parameter="manage_buildings", default=True
                ),
                set_village_name=self.village_set_name,
            )

    def setup_units(self):
        if not self.builder:
            self.setup_builder()

        if not self.units:
            self.units = TroopManager(wrapper=self.wrapper, village_id=self.village_id)
            self.units.resman = self.resman
        self.units.max_batch_size = self.get_config(
            section="units", parameter="batch_size", default=25
        )

        # set village templates
        unit_config = self.get_village_config(
            self.village_id, parameter="units", default=None
        )
        if not unit_config:
            self.logger.warning(
                "Village %d does not have 'building' config override!" % self.village_id
            )
            unit_config = self.get_config(
                section="units", parameter="default", default="basic"
            )
        self.units.template = TemplateManager.get_template(
            category="troops", template=unit_config, output_json=True
        )
        entry = self.units.get_template_action(self.builder.levels)

        if entry and self.units.wanted != entry["build"]:
            # update wanted units if template has changed
            self.logger.info(
                "%s as wanted units for current village" % (str(entry["build"]))
            )
            self.units.wanted = entry["build"]

        disabled_units = []
        if not self.get_config(
            section="world", parameter="archers_enabled", default=True
        ):
            disabled_units.extend(["archer", "marcher"])

        if not self.get_config(
            section="world", parameter="building_destruction_enabled", default=True
        ):
            disabled_units.extend(["ram", "catapult"])

        if self.units.wanted_levels != {}:
            # Remove disabled units
            for disabled in disabled_units:
                self.units.wanted_levels.pop(disabled, None)
            self.logger.info(
                "%s as wanted upgrades for current village"
                % (str(self.units.wanted_levels))
            )

    def run_recruit(self):
        self.setup_units()
        # get total amount of troops in village
        self.units.update_totals()

        if self.get_config(section="units", parameter="recruit", default=False):
            self.units.can_fix_queue = self.get_config(
                section="units", parameter="remove_manual_queued", default=False
            )
            self.units.randomize_unit_queue = self.get_config(
                section="units", parameter="randomize_unit_queue", default=True
            )
            if "research" in self.resman.requested:
                self.logger.info("Not recruiting because research is needed")
                for x in list(self.resman.requested.keys()):
                    if "recruitment_" in x:
                        self.resman.requested.pop(f"{x}", None)
            # prioritize_building: will only recruit when builder has sufficient funds for queue items
            elif (
                self.get_village_config(
                    self.village_id, parameter="prioritize_building", default=False
                )
                and not self.resman.can_recruit()
            ):
                self.logger.info(
                    "Not recruiting because builder has insufficient funds"
                )
                for x in list(self.resman.requested.keys()):
                    if "recruitment_" in x:
                        self.resman.requested.pop(f"{x}", None)
            elif (
                self.get_village_config(
                    self.village_id, parameter="prioritize_snob", default=False
                )
                and self.snobman
                and self.snobman.can_snob
                and self.snobman.is_incomplete
            ):
                self.logger.info("Not recruiting because snob has insufficient funds")
                for x in list(self.resman.requested.keys()):
                    if "recruitment_" in x:
                        self.resman.requested.pop(f"{x}", None)
            else:
                disabled_units = []
                if not self.get_config(
                    section="world", parameter="archers_enabled", default=True
                ):
                    disabled_units.extend(["archer", "marcher"])

                if not self.get_config(
                    section="world",
                    parameter="building_destruction_enabled",
                    default=True,
                ):
                    disabled_units.extend(["ram", "catapult"])
                # do a build run for every
                for building in self.units.wanted:
                    if not self.builder.get_level(building):
                        self.logger.debug(
                            "Recruit of %s will be ignored because building is not (yet) available"
                            % building
                        )
                        continue
                    self.units.start_update(building, disabled_units)

    def run_market(self):
        if not self.builder:
            self.setup_builder()

        if self.get_config(
            section="market", parameter="auto_trade", default=False
        ) and self.builder.get_level("market"):
            self.logger.info("Managing market")
            if self.rep_man.trade_got_accepted:
                self.logger.info(
                    "Our trade got accepted by someone, resetting trading."
                )
                self.resman.last_trade = 0
            self.resman.trade_max_per_hour = self.get_config(
                section="market", parameter="trade_max_per_hour", default=1
            )
            self.resman.trade_max_duration = self.get_config(
                section="market", parameter="max_trade_duration", default=1
            )
            if self.get_config(
                section="market", parameter="trade_multiplier", default=False
            ):
                self.resman.trade_bias = self.get_config(
                    section="market", parameter="trade_multiplier_value", default=1.0
                )
            self.resman.manage_market(
                drop_existing=self.get_config(
                    section="market", parameter="auto_remove", default=True
                )
            )
        if self.get_config(
            section="world", parameter="trade_for_premium", default=False
        ) and self.get_village_config(
            self.village_id, parameter="trade_for_premium", default=False
        ):
            # Set the parameter correctly when the config says so.
            self.resman.do_premium_trade = True
            self.resman.do_premium_stuff()

    def run_attacks(self):
        if not self.units:
            self.setup_units()
        if not self.builder:
            self.setup_builder()
        # Forced peace?
        forced_peace_times = self.get_config(
            section="farms", parameter="forced_peace_times", default=[]
        )
        forced_peace = False
        forced_peace_today = False
        forced_peace_today_start = None
        for time_pairs in forced_peace_times:
            start_dt = datetime.strptime(time_pairs["start"], "%d.%m.%y %H:%M:%S")
            end_dt = datetime.strptime(time_pairs["end"], "%d.%m.%y %H:%M:%S")
            now = datetime.now()
            if start_dt.date() == datetime.today().date():
                forced_peace_today = True
                forced_peace_today_start = start_dt
            if now > start_dt and now < end_dt:
                self.logger.debug(
                    "Currently in a forced peace time! No attacks will be send."
                )
                forced_peace = True
                break

        resources_full = False
        if self.resman and self.resman.any_resource_full():
            self.logger.warning(
                "Resources full for village! Not sending out farming units nor are we gathering resources!"
            )
            resources_full = True

        # attack management
        if not forced_peace and not resources_full and self.units.can_attack:

            if not self.area:
                self.area = Map(wrapper=self.wrapper, village_id=self.village_id)
            self.area.get_map()
            if self.area.villages:
                self.units.can_scout = self.get_config(
                    section="farms", parameter="force_scout_if_available", default=True
                )
                self.logger.info(
                    "%d villages from map cache, (your location: %s)"
                    % (
                        len(self.area.villages),
                        ":".join([str(x) for x in self.area.my_location]),
                    )
                )
                if not self.attack:
                    self.attack = AttackManager(
                        wrapper=self.wrapper,
                        village_id=self.village_id,
                        troopmanager=self.units,
                        map=self.area,
                    )
                    self.attack.repman = self.rep_man
                if forced_peace_today:
                    self.logger.info("Forced peace time coming up today!")
                    self.attack.forced_peace_time = forced_peace_today_start

                self.attack.target_high_points = self.get_config(
                    section="farms", parameter="attack_higher_points", default=False
                )
                self.attack.farm_minpoints = self.get_config(
                    section="farms", parameter="min_points", default=24
                )
                self.attack.farm_maxpoints = self.get_config(
                    section="farms", parameter="max_points", default=1080
                )
                self.attack.farm_radius = self.get_config(
                    section="farms", parameter="search_radius", default=50
                )
                self.attack.farm_default_wait = self.get_config(
                    section="farms", parameter="default_away_time", default=1200
                )
                self.attack.farm_high_prio_wait = self.get_config(
                    section="farms", parameter="full_loot_away_time", default=1800
                )
                self.attack.farm_low_prio_wait = self.get_config(
                    section="farms", parameter="low_loot_away_time", default=7200
                )
                entry = self.units.get_template_action(self.builder.levels)
                self.logger.debug(f"Building levels: {self.builder.levels}")
                self.logger.debug(f"Template: {entry}")
                if self.units.total_troops == {}:
                    self.units.update_totals()

                if entry:
                    self.attack.template = entry["farm"]
                if (
                    self.get_config(section="farms", parameter="farm", default=False)
                    and not self.def_man.under_attack
                ):
                    self.attack.extra_farm = self.get_village_config(
                        self.village_id, parameter="additional_farms", default=[]
                    )
                    self.attack.max_farms = self.get_config(
                        section="farms", parameter="max_farms", default=25
                    )
                    self.attack.run()
        # Gathering too
        self.units.can_gather = self.get_village_config(
            self.village_id, parameter="gather_enabled", default=False
        )
        if not resources_full:
            disabled_units = []
            if not self.get_config(
                section="world", parameter="archers_enabled", default=True
            ):
                disabled_units.extend(["archer", "marcher"])

            if not self.get_config(
                section="world", parameter="building_destruction_enabled", default=True
            ):
                disabled_units.extend(["ram", "catapult"])
            if not self.def_man or not self.def_man.under_attack:
                self.units.gather(
                    selection=self.get_village_config(
                        self.village_id, parameter="gather_selection", default=1
                    ),
                    disabled_units=disabled_units,
                )

    def run_flags(self):
        pass

    def run_defman(self):
        pass

    def run_research(self):
        if not self.units:
            self.logger.debug("Skipping research for now.")
            return False
        if (
            self.get_config(section="units", parameter="upgrade", default=False)
            and self.units.wanted_levels != {}
        ):
            disabled_units = []
            if not self.get_config(
                section="world", parameter="archers_enabled", default=True
            ):
                disabled_units.extend(["archer", "marcher"])

            if not self.get_config(
                section="world", parameter="building_destruction_enabled", default=True
            ):
                disabled_units.extend(["ram", "catapult"])
            # remove disabled units
            for disabled in disabled_units:
                for disabled in disabled_units:
                    self.units.wanted_levels.pop(disabled, None)
            self.units.attempt_upgrade()

    def run_snob(self):
        if (
            self.get_village_config(self.village_id, parameter="snobs", default=None)
            and self.builder.levels["snob"] > 0
            and not "snob" in self.builder.waits_building
        ):
            if not self.snobman:
                self.snobman = SnobManager(
                    wrapper=self.wrapper, village_id=self.village_id
                )
                self.snobman.troop_manager = self.units
                self.snobman.resman = self.resman
            self.snobman.wanted = self.get_village_config(
                self.village_id, parameter="snobs", default=0
            )
            self.snobman.building_level = self.builder.get_level("snob")
            self.snobman.run()

    def run_quests(self):
        if self.get_config(section="world", parameter="quests_enabled", default=False):
            if self.get_quests():
                # self.logger.info("There where completed quests, re-running function")
                self.wrapper.reporter.report(
                    self.village_id, "TWB_QUEST", "Completed quest"
                )

            if self.get_quest_rewards():
                self.wrapper.reporter.report(
                    self.village_id, "TWB_QUEST", "Collected quest reward(s)"
                )

    def run(self, config=None, first_run=False):
        # setup and check if village still exists / is accessible
        self.config = config
        self.wrapper.delay = self.get_config(
            section="bot", parameter="delay_factor", default=1.0
        )
        if not self.village_id:
            data = self.wrapper.get_url("game.php?screen=overview&intro")
            if data:
                self.game_data = Extractor.game_state(data)
            if self.game_data:
                self.village_id = str(self.game_data["village"]["id"])
                self.logger = logging.getLogger(
                    "Village %s" % self.game_data["village"]["name"]
                )
                self.logger.info("Read game state for village")
        else:
            data = self.wrapper.get_url(
                "game.php?village=%s&screen=overview" % self.village_id
            )
            if data:
                self.game_data = Extractor.game_state(data)
                self.logger = logging.getLogger(
                    "Village %s" % self.game_data["village"]["name"]
                )
                self.logger.info("Read game state for village")
                self.wrapper.reporter.report(
                    self.village_id,
                    "TWB_START",
                    "Starting run for village: %s" % self.game_data["village"]["name"],
                )
                self.get_daily_reward(data)

        if not self.game_data:
            self.logger.error(
                "Error reading game data for village %s" % self.village_id
            )
            return None

        if (
            self.village_set_name
            and self.game_data["village"]["name"] != self.village_set_name
        ):
            self.logger.name = "Village %s" % self.village_set_name

        if not self.get_config(section="villages", parameter=self.village_id):
            return None
        if self.get_config(
            section="server", parameter="server_on_twplus", default=False
        ):
            self.twp.run(world=self.get_config(section="server", parameter="world"))

        vdata = self.get_config(section="villages", parameter=self.village_id)
        if not self.get_village_config(
            self.village_id, parameter="managed", default=False
        ):
            return False
        if not self.game_data:
            return False
        if not self.resman:
            self.resman = ResourceManager(
                wrapper=self.wrapper, village_id=self.village_id
            )

        self.resman.update(self.game_data)
        self.wrapper.reporter.report(
            self.village_id, "TWB_PRE_RESOURCE", str(self.resman.actual)
        )

        if not self.rep_man:
            self.rep_man = ReportManager(
                wrapper=self.wrapper, village_id=self.village_id
            )
        self.rep_man.read(full_run=False)

        if not self.def_man:
            self.def_man = DefenceManager(
                wrapper=self.wrapper, village_id=self.village_id
            )
            self.def_man.map = self.area

        if not self.def_man.units:
            self.def_man.units = self.units

        last_attack = self.def_man.under_attack
        self.def_man.manage_flags_enabled = self.get_config(
            section="world", parameter="flags_enabled", default=False
        )
        self.def_man.support_factor = self.get_village_config(
            self.village_id, "support_others_factor", default=0.25
        )

        self.def_man.allow_support_send = self.get_village_config(
            self.village_id, parameter="support_others", default=False
        )
        self.def_man.allow_support_recv = self.get_village_config(
            self.village_id, parameter="request_support_on_attack", default=False
        )
        self.def_man.auto_evacuate = self.get_village_config(
            self.village_id, parameter="evacuate_fragile_units_on_attack", default=False
        )
        self.def_man.update(
            data.text,
            with_defence=self.get_config(
                section="units", parameter="manage_defence", default=False
            ),
        )

        if self.def_man.under_attack and not last_attack:
            self.logger.warning("Village under attack!")
            self.wrapper.reporter.report(
                self.village_id,
                "TWB_ATTACK",
                "Village: %s under attack" % self.game_data["village"]["name"],
            )
            self.wrapper.discord.send("Village: %s under attack" % self.game_data["village"]["name"])

        # setup and check if village still exists / is accessible
        # Load troops in village...
        if not self.units:
            self.setup_units()
        for u in Extractor.units_in_village(data):
            k, v = u
            self.units.troops[k] = v

        # Act more human like and do things in an random(ish) order - Market is always last?
        functions = [
            self.run_quests,
            self.run_builder,
            self.run_research,
            self.run_snob,
            self.run_recruit,
            self.run_attacks,
        ]
        random.shuffle(functions)
        for func in functions:
            func()
        # self.run_quests()

        # self.run_builder()
        # self.run_research()

        # self.run_snob()

        # # recruitment management
        # self.run_recruit()

        #
        to_dell = []
        for x in self.resman.requested:
            if all(res == 0 for res in self.resman.requested[x].values()):
                # remove empty requests!
                to_dell.append(x)

        for x in to_dell:
            self.resman.requested.pop(x)

        self.logger.debug("Current resources: %s" % str(self.resman.actual))
        self.logger.debug("Requested resources: %s" % str(self.resman.requested))

        # self.run_attacks()

        # market management
        self.run_market()

        res = self.wrapper.get_action(village_id=self.village_id, action="overview")
        self.game_data = Extractor.game_state(res)
        self.determine_next_back(res)
        self.resman.update(self.game_data)
        if self.get_config(
            section="world", parameter="trade_for_premium", default=False
        ) and self.get_village_config(
            self.village_id, parameter="trade_for_premium", default=False
        ) and self.builder.get_level("market"):
            # Set the parameter correctly when the config says so.
            self.resman.do_premium_trade = True
            self.resman.do_premium_stuff()
        
        self.set_cache_vars()
        self.logger.info("Village cycle done, returning to overview")
        self.wrapper.reporter.report(
            self.village_id, "TWB_POST_RESOURCE", str(self.resman.actual)
        )
        self.wrapper.reporter.add_data(
            self.village_id,
            data_type="village.resources",
            data=json.dumps(self.resman.actual),
        )
        self.wrapper.reporter.add_data(
            self.village_id,
            data_type="village.buildings",
            data=json.dumps(self.builder.levels),
        )
        self.wrapper.reporter.add_data(
            self.village_id,
            data_type="village.troops",
            data=json.dumps(self.units.total_troops),
        )
        self.wrapper.reporter.add_data(
            self.village_id, data_type="village.config", data=json.dumps(vdata)
        )

    def get_quests(self):
        result = Extractor.get_quests(self.wrapper.last_response)
        if result:
            qres = self.wrapper.get_api_action(
                action="quest_complete",
                village_id=self.village_id,
                params={"quest": result, "skip": "false"},
            )
            if qres:
                self.logger.info("Completed quest: %s" % str(result))
                return True
        self.logger.debug("There where no completed quests")
        return False

    def get_quest_rewards(self):
        result = self.wrapper.get_api_data(
            action="quest_popup",
            village_id=self.village_id,
            params={"screen": "new_quests", "tab": "main-tab", "quest": 0},
        )
        # The data is escaped for JS, so unescape it before sending it to the extractor.
        rewards = Extractor.get_quest_rewards(
            decode(result["response"]["dialog"], "unicode-escape")
        )
        for reward in rewards:
            # First check if there is enough room for storing the reward
            for t_resource in reward["reward"]:
                if (
                    self.resman.storage - self.resman.actual[t_resource]
                    < reward["reward"][t_resource]
                ):
                    self.logger.info(
                        f"Not enough room to store the {t_resource} part of the reward"
                    )
                    return False

            qres = self.wrapper.post_api_data(
                action="claim_reward",
                village_id=self.village_id,
                params={"screen": "new_quests"},
                data={"reward_id": reward["id"]},
            )
            if qres:
                if qres["response"] == False:
                    self.logger.debug(f"Error getting reward! {qres}")
                    return False
                else:
                    self.logger.info("Got quest reward: %s" % str(reward))
                    for t_resource in reward["reward"]:
                        self.resman.actual[t_resource] += reward["reward"][t_resource]

        self.logger.debug("There where no (more) quest rewards")
        return len(rewards) > 0

    def get_daily_reward(self, res):
        is_daily_reward = Extractor.game_state(res)
        self.logger.debug("Checking daily reward - %s" % is_daily_reward["player"]["new_daily_bonus"])
        if is_daily_reward["player"]["new_daily_bonus"] == "1":
            result_get = self.wrapper.get_url("game.php?village=%s&screen=info_player&mode=daily_bonus" % self.village_id)
            reward_count_unlocked = Extractor.get_daily_reward(result_get)
            if reward_count_unlocked:
                self.logger.debug("Day %s reward is available" % reward_count_unlocked)
                self.wrapper.post_url(
                    url="game.php?village=%s&screen=daily_bonus&ajaxaction=open"
                    % self.village_id,
                    data={"day": reward_count_unlocked, "h": self.wrapper.last_h, "from_screen": "profile"},
                )
        return False

    def set_cache_vars(self):
        village_entry = {
            "name": self.game_data["village"]["name"],
            "public": self.area.in_cache(self.village_id) if self.area else None,
            "resources": self.resman.actual,
            "required_resources": self.resman.requested,
            "available_troops": self.units.troops,
            "buidling_levels": self.builder.levels,
            "building_queue": self.builder.queue,
            "troops": self.units.total_troops,
            "under_attack": self.def_man.under_attack,
            "last_run": int(time.time()),
        }
        self.set_cache(self.village_id, entry=village_entry)

    def set_cache(self, village_id, entry):
        t_path = os.path.join("cache", "managed", village_id + ".json")
        with open(t_path, "w") as f:
            return json.dump(entry, f)
