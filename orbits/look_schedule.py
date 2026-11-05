"""Apply saved Looks from a systemd user timer without a running GTK window."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


def clock_minutes(value):
    if not isinstance(value,str) or not re.fullmatch(r"\d{2}:\d{2}",value):raise ValueError("Use HH:MM times")
    hours,minutes=map(int,value.split(":"))
    if hours>23 or minutes>59:raise ValueError("Use times between 00:00 and 23:59")
    return hours*60+minutes


def validate(config):
    if not isinstance(config,dict):raise ValueError("Invalid Look schedule")
    result=dict(config)
    if "enabled" in result and not isinstance(result["enabled"],bool):raise ValueError("Enabled must be true or false")
    if result.get("mode","daynight") not in ("daynight","weekday"):raise ValueError("Unknown schedule type")
    for key in ("day_start","night_start"):clock_minutes(result.get(key,"07:00" if key=="day_start" else "19:00"))
    if result.get("day_start","07:00")==result.get("night_start","19:00"):raise ValueError("Day and night must start at different times")
    for key in ("day","night"):
        if result.get(key) and (not isinstance(result[key],str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}",result[key])):raise ValueError("Invalid saved Look ID")
    weekdays=result.get("weekdays",{})
    if not isinstance(weekdays,dict) or any(key not in map(str,range(7)) for key in weekdays):raise ValueError("Invalid weekday choices")
    for look in weekdays.values():
        if look and (not isinstance(look,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}",look)):raise ValueError("Invalid weekday Look ID")
    return result


def choose(config,now=None):
    config=validate(config);now=now or datetime.now()
    if config.get("mode","daynight")=="weekday":return config.get("weekdays",{}).get(str(now.weekday()))
    minute=now.hour*60+now.minute
    day=clock_minutes(config.get("day_start","07:00"));night=clock_minutes(config.get("night_start","19:00"))
    daytime=day<=minute<night if day<night else minute>=day or minute<night
    return config.get("day" if daytime else "night")


def selection_key(config,now=None):
    """Each scheduled period is applied once, even if adjacent periods use the same Look."""
    now=now or datetime.now()
    selected=choose(config,now)
    if not selected:return None
    if config.get("mode","daynight")=="weekday":return f"{now.date()}:weekday:{selected}"
    minute=now.hour*60+now.minute
    day=clock_minutes(config.get("day_start","07:00"));night=clock_minutes(config.get("night_start","19:00"))
    daytime=day<=minute<night if day<night else minute>=day or minute<night
    start=day if daytime else night
    date=(now-timedelta(days=1)).date() if minute<start else now.date()
    return f"{date}:{'day' if daytime else 'night'}:{selected}"


def unit_contents(python=None,root=None):
    from orbits.rotation import unit_quote
    root=root or Path(__file__).resolve().parent.parent
    service=("[Unit]\nDescription=Apply the scheduled Orbits Look\n\n[Service]\nType=oneshot\n"
             f"WorkingDirectory={unit_quote(root)}\nExecStart={unit_quote(python or sys.executable)} -m orbits.look_schedule\nTimeoutStartSec=5min\n")
    timer="[Unit]\nDescription=Check the Orbits Look schedule\n\n[Timer]\nOnStartupSec=20s\nOnCalendar=*-*-* *:*:00\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n"
    return service,timer


def load_config():
    from orbits.config import CONFIG_DIR
    try:return validate(json.loads((CONFIG_DIR/"look-schedule.json").read_text()))
    except (OSError,ValueError,TypeError):return {"enabled":False,"mode":"daynight","day_start":"07:00","night_start":"19:00","weekdays":{}}


def configure(config):
    from gi.repository import GLib
    from orbits.config import CONFIG_DIR
    from orbits.rotation import systemctl
    config=validate(config)
    from orbits.looks import LOOKS_DIR
    referenced=[config.get(key) for key in ("day","night")] if config.get("mode","daynight")=="daynight" else list(config.get("weekdays",{}).values())
    if config.get("enabled") and (not any(referenced) or any(look and not (LOOKS_DIR/look/"look.json").is_file() for look in referenced)):
        raise ValueError("Choose saved Looks before enabling the schedule")
    config["revision"]=uuid.uuid4().hex
    path=CONFIG_DIR/"look-schedule.json"
    folder=Path(GLib.get_user_config_dir())/"systemd/user";folder.mkdir(parents=True,exist_ok=True)
    targets=[folder/f"orbits-look.{suffix}" for suffix in ("service","timer")]
    previous={p:p.read_bytes() if p.exists() else None for p in [path,*targets]}
    try:
        path.write_text(json.dumps(config,indent=2))
        for target,text in zip(targets,unit_contents()):target.write_text(text)
        systemctl("daemon-reload")
        systemctl("enable" if config.get("enabled") else "disable","--now","orbits-look.timer")
        if config.get("enabled"):systemctl("restart","orbits-look.timer")
    except Exception:
        for target,content in previous.items():
            if content is None:target.unlink(missing_ok=True)
            else:target.write_bytes(content)
        try:subprocess.run(["systemctl","--user","daemon-reload"],capture_output=True,timeout=20)
        except (OSError,subprocess.TimeoutExpired):pass
        raise


def main():
    import gi
    gi.require_version("Gdk","4.0")
    import fcntl
    from gi.repository import GLib
    from orbits.config import CONFIG_DIR
    from orbits import looks
    with (CONFIG_DIR/"look-schedule.lock").open("w") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        config=load_config()
        if not config.get("enabled"):return 0
        now=datetime.now()
        selected=choose(config,now)
        if not selected:return 0
        # Resolve the ID only within the saved-Looks directory, never to an arbitrary path.
        folder=(looks.LOOKS_DIR/selected).resolve()
        if not folder.is_relative_to(looks.LOOKS_DIR.resolve()) or folder==looks.LOOKS_DIR.resolve():
            print("Scheduled Look path is invalid",file=sys.stderr);return 1
        path=CONFIG_DIR/"look-schedule-last.json"
        token=config.get("revision","")+":"+selection_key(config,now)
        try:
            last=json.loads(path.read_text())
            if last.get("token")==token:return 0
        except (OSError,ValueError):pass
        attempt=CONFIG_DIR/"look-schedule-attempt.json"
        try:
            recent=json.loads(attempt.read_text())
            if recent.get("token")==token and time.time()-recent.get("attempted_at",0)<900:return 0
        except (OSError,ValueError,TypeError):pass
        attempt.write_text(json.dumps({"token":token,"attempted_at":time.time()}))
        try:
            look=json.loads((folder/"look.json").read_text())
            loop=GLib.MainLoop();state={"finished":False,"notes":[]}
            def done(notes):state.update(finished=True,notes=notes);loop.quit()
            window=type("ScheduledWindow",(),{"pages":{},"toast":lambda _self,text,**_kwargs:print(text)})()
            applier=looks.LookApplier(look,window,done=done,components=looks.DESKTOP_COMPONENTS)
            applier.start()
            if not state["finished"]:loop.run()
            if state["notes"]:
                print("; ".join(state["notes"]),file=sys.stderr);return 1
            path.write_text(json.dumps({"token":token,"applied_at":time.time()}))
        except Exception as error:
            print(f"Scheduled Look failed: {error}",file=sys.stderr);return 1
    return 0


if __name__=="__main__":sys.exit(main())
