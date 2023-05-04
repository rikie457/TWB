import os
import json
import collections
import subprocess
import psutil


class DataReader:
    @staticmethod
    def cache_grab(cache_location):
        output = {}
        c_path = os.path.join("../cache", cache_location)
        for existing in os.listdir(c_path):
            if not existing.endswith(".json"):
                continue
            t_path = os.path.join("../cache", cache_location, existing)
            with open(t_path, "r") as f:
                try:
                    output[existing.replace(".json", "")] = json.load(f)
                except Exception as e:
                    print(
                        "Cache read error for %s: %s. Removing broken entry"
                        % (t_path, str(e))
                    )
                    f.close()
                    os.remove(t_path)

        return output

    @staticmethod
    def template_grab(template_location):
        output = []
        template_location = template_location.replace(".", "/")
        c_path = os.path.join("../", template_location)
        for existing in os.listdir(c_path):
            if not existing.endswith(".txt"):
                continue
            output.append(existing.split(".")[0])
        return output

    @staticmethod
    def config_grab():
        with open("../config.json", "r") as f:
            return json.load(f)

    @staticmethod
    def config_set(parameter, value):
        try:
            value = json.loads(value)
        except:
            pass
        with open("../config.json", "r") as config_file:
            template = json.load(config_file, object_pairs_hook=collections.OrderedDict)
            if "." in parameter:
                section, param = parameter.split(".")
                template[section][param] = value
            else:
                template[parameter] = value
            with open("../config.json", "w") as newcf:
                json.dump(template, newcf, indent=2, sort_keys=False)
                print("Deployed new configuration file")
                return True

    @staticmethod
    def village_config_set(village_id, parameter, value):
        try:
            value = json.loads(value)
        except:
            pass
        with open("../config.json", "r") as config_file:
            template = json.load(config_file, object_pairs_hook=collections.OrderedDict)
            if village_id not in template["villages"]:
                return False
            template["villages"][str(village_id)][parameter] = value
            with open("../config.json", "w") as newcf:
                json.dump(template, newcf, indent=2, sort_keys=False)
                print("Deployed new configuration file")
                return True

    @staticmethod
    def get_session():
        c_path = os.path.join("../cache", "session.json")
        if not os.path.exists(c_path):
            return {"raw": "", "endpoint": "None", "server": "None", "world": "None"}
        with open(c_path, "r") as session_file:
            session_data = json.load(session_file)
            cookies = []
            for c in session_data["cookies"]:
                cookies.append("%s=%s" % (c, session_data["cookies"][c]))
            session_data["raw"] = ";".join(cookies)
            return session_data
    @staticmethod
    def set_session(cinp):
        c_path = os.path.join("../cache", "session.json")
        cookies = {}
        cinp = cinp.strip()
        for itt in cinp.split(';'):
            itt = itt.strip()
            kvs = itt.split("=")
            k = kvs[0]
            v = '='.join(kvs[1:])
            cookies[k] = v

        with open(c_path, 'r') as session_file:
            session = json.load(session_file)

        with open(c_path, 'w') as f:
            new_session = {
                'endpoint': session["endpoint"],
                'server': session["server"],
                'cookies': cookies
            }
            json.dump(new_session, f)
            print("Saved Session!")
            return True


class BuildingTemplateManager:
    @staticmethod
    def template_cache_list():
        c_path = os.path.join("../templates", "builder")
        output = {}
        for existing in os.listdir(c_path):
            if not existing.endswith(".txt"):
                continue
            with open(
                os.path.join("../templates", "builder", existing), "r"
            ) as template_file:
                output[existing] = BuildingTemplateManager.template_to_dict(
                    [x.strip() for x in template_file.readlines()]
                )
        return output

    @staticmethod
    def template_to_dict(t_list):
        out_data = {}
        rows = []

        for entry in t_list:
            if entry.startswith("#") or ":" not in entry:
                continue
            building, next_level = entry.split(":")
            next_level = int(next_level)
            old = 0
            if building in out_data:
                old = out_data[building]
            rows.append({"building": building, "from": old, "to": next_level})
            out_data[building] = next_level

        return rows


class MapBuilder:
    @staticmethod
    def build(villages, current_village=None, size=None):
        out_map = {}
        min_x = 999
        max_x = 0
        min_y = 999
        max_y = 0

        current_location = None

        grid_vils = {}

        extra_data = {}

        for v in villages:
            vdata = villages[v]
            x, y = vdata["location"]
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x

            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            if current_village and vdata["id"] == current_village:
                current_location = vdata["location"]
                extra_data["owner"] = vdata["owner"]
                extra_data["tribe"] = vdata["tribe"]
            grid_vils["%d:%d" % (x, y)] = vdata

        if current_location and size:
            min_x = current_location[0] - size
            min_y = current_location[1] - size
            max_x = current_location[0] + size
            max_y = current_location[1] + size

        for location_x in range(min_x, max_x):
            if location_x not in out_map:
                out_map[location_x - min_x] = {}
            ylocs = {}
            for location_y in range(min_y, max_y):
                location = "%d:%d" % (location_x, location_y)
                if location in grid_vils:
                    ylocs[location_y - min_y] = grid_vils[location]
                else:
                    ylocs[location_y - min_y] = None
            out_map[location_x - min_x] = ylocs

        return {"grid": out_map, "extra": extra_data}


class BotManager:
    proc = None

    def kill(self, proc_pid):
        process = psutil.Process(proc_pid)
        for proc in process.children(recursive=True):
            proc.kill()
        process.kill()
        self.proc = None

    def is_running(self):
        if self.proc is None or self.proc is False:
            return False
        if self.proc.poll() is not None or self.proc.poll() is not False:
            return True
        self.proc = False
        return False

    def start(self):
        wd = os.path.join(os.getcwd(), "..")
        self.proc = subprocess.Popen("python twb.py", cwd=wd, stdout=subprocess.PIPE, shell=True)
        print("Bot started successfully")

    def stop(self):
        if self.is_running():
            self.kill(self.proc.pid)
            print("Bot stopped successfully")
