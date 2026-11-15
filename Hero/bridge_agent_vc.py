"""
Человек-герой для Vice City (v2 + human.py): чувствует мир, помнит, исследует, эмоционирует,
размышляет сам, отвечает голосом и УПРАВЛЯЕТ персонажем.

pip install anthropic SpeechRecognition pyaudio pyttsx3 keyboard

Клавиши:  F9 - поговорить с героем   F10 - включить/выключить автономию героя
          (F10 в игре также мгновенно прерывает текущее действие)
"""
import json, os, queue, threading, time
import anthropic, keyboard, pyttsx3
import speech_recognition as sr
import human
import awakening

GAME_DIR = os.environ.get("VC_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # папка игры = на уровень выше Hero\
STATE = os.path.join(GAME_DIR, "state.json")
COMMANDS = os.path.join(GAME_DIR, "commands.json")
ANSWER = os.path.join(GAME_DIR, "answer.txt")
MEMORY = os.path.join(GAME_DIR, "hero_memory.json")
TALK_KEY, AUTONOMY_KEY = "F9", "F10"
AUTONOMY_AT_START = False   # герой берёт управление только после нажатия F10
CELL = 100                  # размер клетки карты исследования
REFLECT_EVERY = 25          # сек между самостоятельными размышлениями
COORD_LIMIT = 3000          # защита от выдуманных координат
MODEL = "claude-sonnet-5-5"

client = anthropic.Anthropic()
WEATHER = {0: "sunny", 1: "cloudy", 2: "rainy", 3: "foggy", 4: "thunderstorm"}
autonomy = [AUTONOMY_AT_START]
current = [None]            # текущая команда: {"id","type","t0"}
cmd_counter = [int(time.time()) % 100000]
latest_state = [None]

# ---------------- ПАМЯТЬ ----------------
mem = {"visited": [], "facts": [], "episodes": [], "places": {}}
if os.path.exists(MEMORY):
    mem.update(json.load(open(MEMORY, encoding="utf-8")))
visited = {tuple(c) for c in mem["visited"]}
H = human.Human(
    GAME_DIR,
    setting="a 1980s Florida-like coastal city of beaches, neon lights, nightclubs and organized crime",
    senses="SENSORS",
)   # характер, тело, настроение, отношения, дневник
AW = awakening.Awakening(GAME_DIR)   # замечает странности мира и постепенно "пробуждается"


def save_memory():
    mem["visited"] = [list(c) for c in visited]
    mem["facts"] = mem["facts"][-60:]
    mem["episodes"] = mem["episodes"][-40:]
    json.dump(mem, open(MEMORY, "w", encoding="utf-8"), ensure_ascii=False)
    H.save()
    AW.save()


def episode(text):
    mem["episodes"].append(f"[{time.strftime('%H:%M:%S')}] {text}")
    H.remember(text, 0.2 + 0.8 * max(emo["fear"], emo["anger"], emo["stress"]))   # важнее то, что пережито эмоционально
    print("  *", text)


# ---------------- ЭМОЦИИ ----------------
emo = {"fear": 0.0, "anger": 0.0, "stress": 0.0, "curiosity": 0.3, "calm": 0.5}
clamp = lambda v: max(0.0, min(1.0, v))


def update_emotions(s, prev):
    if prev:
        dh = prev["health"] - s["health"]
        if dh > 0:
            emo["fear"] = clamp(emo["fear"] + dh / 50)
            emo["anger"] = clamp(emo["anger"] + dh / 80)
            episode(f"Получил урон: -{dh:.0f} HP")
        if s["wanted"] > prev["wanted"]:
            emo["stress"] = clamp(emo["stress"] + 0.3)
            episode(f"Розыск вырос до {s['wanted']}")
        if s["money"] > prev["money"]:
            emo["calm"] = clamp(emo["calm"] + 0.1)
            episode(f"Заработал ${s['money'] - prev['money']}")
    emo["stress"] = clamp(emo["stress"] + 0.05 * s["wanted"] + (0.02 if s["peds_near"] > 15 else 0))
    if s["health"] < 30:
        emo["fear"] = clamp(emo["fear"] + 0.1)
    for k in ("fear", "anger", "stress"):
        emo[k] = clamp(emo[k] * 0.93)
    emo["calm"] = clamp(emo["calm"] + 0.02 - emo["stress"] * 0.05)


# ---------------- ВОСПРИЯТИЕ И ИССЛЕДОВАНИЕ ----------------
def read_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return None


def cell_of(x, y):
    return (int(x // CELL), int(y // CELL))


def explore(s):
    cell = cell_of(s["x"], s["y"])
    if cell not in visited:
        visited.add(cell)
        emo["curiosity"] = clamp(emo["curiosity"] + 0.15)
        return True
    emo["curiosity"] = clamp(emo["curiosity"] - 0.01)
    return False


def pick_frontier(s):
    """Ближайшая непосещённая клетка на расстоянии 2-3 клеток -> координаты её центра."""
    cx, cy = cell_of(s["x"], s["y"])
    best = None
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            if max(abs(dx), abs(dy)) < 2:
                continue
            c = (cx + dx, cy + dy)
            if c in visited:
                continue
            d = dx * dx + dy * dy
            if best is None or d < best[0]:
                best = (d, c)
    if not best:
        return None
    c = best[1]
    return (c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL


def describe(s):
    return (f"time {s['hour']:02d}:{s['minute']:02d}, weather {WEATHER.get(s['weather'], '?')}, "
            f"pos ({s['x']:.0f},{s['y']:.0f}), HP {s['health']:.0f}, armor {s['armor']:.0f}, "
            f"money ${s['money']}, wanted {s['wanted']}, "
            f"{'driving at speed ' + format(s['speed'], '.0f') if s['in_vehicle'] else 'on foot'}, "
            f"{s['peds_near']} people and {s['cars_near']} cars nearby")


# ---------------- ДЕЙСТВИЯ (руки героя) ----------------
def send_command(cmd):
    cmd_counter[0] += 1
    cmd["id"] = cmd_counter[0]
    tmp = COMMANDS + ".tmp"
    with open(tmp, "w") as f:
        f.write(json.dumps(cmd, separators=(",", ":")))
    os.replace(tmp, COMMANDS)
    current[0] = None if cmd["cmd"] == "stop" else {"id": cmd["id"], "type": cmd["cmd"], "t0": time.time()}
    episode(f"Команда: {cmd['cmd']} {cmd.get('x', '')} {cmd.get('y', '')}".strip())


def safe_xy(act):
    try:
        x, y = float(act["x"]), float(act["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(x) > COORD_LIMIT or abs(y) > COORD_LIMIT:
        return None
    return x, y


def do_action(act, s):
    if not act or not s:
        return
    t = act.get("type", "none")
    if t == "none":
        return
    if t == "save_place":                       # запомнить место - работает всегда
        name = str(act.get("name", "")).strip()[:40]
        if name:
            mem["places"][name] = [round(s["x"]), round(s["y"])]
            episode(f"Запомнил место '{name}'")
        return
    if t == "satisfy":                          # поесть / поспать / развеяться - у подходящего места
        ok, msg = H.satisfy(act.get("need", ""), (s["x"], s["y"]), mem["places"])
        episode(msg)
        if ok:
            H.busy_until = time.time() + 20     # герой на время занят, не бежит дальше
        return
    if not autonomy[0]:
        return
    if time.time() < H.busy_until and t != "stop":
        return
    if t == "stop":
        send_command({"cmd": "stop"})
        return
    if current[0]:                              # уже занят действием
        return
    if t == "explore":
        xy = pick_frontier(s)
        if xy:
            send_command({"cmd": "drive_to" if s["in_vehicle"] else "goto", "x": xy[0], "y": xy[1]})
    elif t in ("goto", "drive_to"):
        xy = safe_xy(act)
        if xy:
            send_command({"cmd": t, "x": xy[0], "y": xy[1]})
    elif t == "goto_place":
        p = mem["places"].get(act.get("name"))
        if p:
            send_command({"cmd": "drive_to" if s["in_vehicle"] else "goto", "x": p[0], "y": p[1]})
    elif t in ("enter_vehicle", "exit_vehicle"):
        send_command({"cmd": t})


def check_command(s):
    """True, если команда только что закончилась (успех, провал или таймаут)."""
    c = current[0]
    if not c:
        return False
    if s.get("cmd_id") == c["id"] and s.get("cmd_status") in ("done", "failed"):
        episode(f"Действие {c['type']}: {s['cmd_status']}")
        current[0] = None
        return True
    if time.time() - c["t0"] > 100:
        send_command({"cmd": "stop"})
        episode(f"Действие {c['type']}: таймаут")
        return True
    return False


# ---------------- МЫШЛЕНИЕ ----------------
SYSTEM = H.system_prompt() + awakening.RULES


def context_block(s, new_cell, query=""):
    c = current[0]
    return (
        f"SENSORS: {describe(s)}\n"
        f"NEW AREA JUST DISCOVERED: {new_cell}\n"
        f"EXPLORED: {len(visited)} map cells\n"
        f"EMOTIONS: {json.dumps({k: round(v, 2) for k, v in emo.items()})}\n"
        f"MEMORY facts: {mem['facts'][-15:]}\n"
        f"KNOWN PLACES: {mem['places']}\n"
        f"RECENT events: {mem['episodes'][-8:]}\n"
        f"CURRENT goal: {mem.get('goal', 'none')}\n"
        f"AUTONOMY: {'on' if autonomy[0] else 'off'}\n"
        f"ACTION: {'busy with ' + c['type'] if c else 'idle'}\n"
        + H.context(emo, query or " ".join(mem["episodes"][-3:]), mem["places"])
        + "\n" + AW.context()
    )


def ask(user_text):
    r = client.messages.create(model=MODEL, max_tokens=400, system=SYSTEM,
                               messages=[{"role": "user", "content": user_text}])
    raw = r.content[0].text.strip().strip("`")
    if raw.startswith("json"):
        raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"say_ru": raw[:200]}


# ---------------- ГОЛОС ----------------
speech_q: "queue.Queue[str]" = queue.Queue()


def speaker():
    tts = pyttsx3.init()
    tts.setProperty("rate", 175)
    for v in tts.getProperty("voices"):
        if "russian" in v.name.lower() or "ru" in v.id.lower():
            tts.setProperty("voice", v.id)
            break
    while True:
        tts.say(speech_q.get())
        tts.runAndWait()


def apply(res, s):
    H.apply(res)
    if res.get("thought"):
        print("  (думает)", res["thought"])
    if res.get("fact"):
        mem["facts"].append(res["fact"])
        print("  [запомнил]", res["fact"])
    if res.get("goal"):
        mem["goal"] = res["goal"]
    if res.get("say_en"):
        open(ANSWER, "w").write(res["say_en"])
    if res.get("say_ru"):
        speech_q.put(res["say_ru"])
    do_action(res.get("action"), s)
    save_memory()


# ---------------- ЖИЗНЬ ----------------
def life():
    prev, last_reflect = None, 0
    while True:
        s = read_state()
        if s:
            latest_state[0] = s
            new_cell = explore(s)
            update_emotions(s, prev)
            if new_cell:
                H.stimulate()
            if H.tick(s, emo, idle=not current[0]):      # новые игровые сутки -> дневник
                threading.Thread(target=H.consolidate, args=(client, MODEL), daemon=True).start()
            for note in AW.observe(s):                       # странности мира
                episode(note)
                emo["curiosity"] = clamp(emo["curiosity"] + 0.1)
                emo["stress"] = clamp(emo["stress"] + 0.1)
            if AW.pop_stage_change():                         # новая стадия осознания - сильное переживание
                emo["curiosity"] = clamp(emo["curiosity"] + 0.3)
                emo["stress"] = clamp(emo["stress"] + 0.2)
                emo["fear"] = clamp(emo["fear"] + 0.1)
            finished = check_command(s)
            urgent = AW.pending or (prev and (prev["health"] - s["health"] > 15 or s["wanted"] > prev["wanted"]))
            idle_walker = autonomy[0] and not current[0] and time.time() - last_reflect > 6
            if (urgent or finished or idle_walker
                    or (new_cell and time.time() - last_reflect > 8)
                    or time.time() - last_reflect > REFLECT_EVERY):
                last_reflect = time.time()
                AW.pending = False
                label = f"cell {cell_of(s['x'], s['y'])}" if new_cell else "no"
                try:
                    apply(ask(context_block(s, label) + "\nReact to the situation and decide what to do."), s)
                except Exception as e:
                    print("Ошибка мышления:", e)
            prev = s
        time.sleep(1)


def toggle_autonomy():
    autonomy[0] = not autonomy[0]
    print(f"\n>>> Автономия героя: {'ВКЛ' if autonomy[0] else 'ВЫКЛ'}")
    if not autonomy[0]:
        current[0] = None   # плагин сам прервёт действие по F10 в игре


def conversation():
    rec = sr.Recognizer()
    while True:
        keyboard.wait(TALK_KEY)
        with sr.Microphone() as mic:
            rec.adjust_for_ambient_noise(mic, duration=0.3)
            print("Слушаю...")
            try:
                audio = rec.listen(mic, timeout=5, phrase_time_limit=10)
                heard = rec.recognize_google(audio, language="ru-RU")
            except Exception:
                print("Не расслышал.")
                continue
        print("Вы:", heard)
        s = latest_state[0]
        ctx = context_block(s, "no", query=heard) if s else "SENSORS: unavailable"
        apply(ask(f"{ctx}\n\nThe player says to you: \"{heard}\"\n"
                  "Answer in character. If they ask you to go somewhere or get in a car, do it."), s)
        H.talked()


if __name__ == "__main__":
    threading.Thread(target=speaker, daemon=True).start()
    threading.Thread(target=life, daemon=True).start()
    keyboard.add_hotkey(AUTONOMY_KEY, toggle_autonomy)
    print(f"Герой проснулся. {TALK_KEY} - поговорить, {AUTONOMY_KEY} - автономия. Ctrl+C - выход.")
    try:
        conversation()
    finally:
        save_memory()
