"""
human.py - слой «человеческой» психологии для героя.

Даёт персонажу: биографию и характер (Big Five), ценности и страхи, тело (голод, усталость,
одиночество, скука), настроение, отношения с людьми (прежде всего с игроком), эпизодическую
память с важностью и забыванием, дневник и «историю жизни», которые пересобираются раз в игровые сутки.

Это модель человеческой психологии, а не сознание: герой ведёт себя как живой человек,
но ничего не чувствует на самом деле.
Свой характер можно задать файлом persona.json рядом с игрой (поля как в DEFAULT_PERSONA).
"""
import copy
import json
import math
import os
import re
import threading
import time

DEFAULT_PERSONA = {
    "name": "Danny Mercer",
    "age": 34,
    "backstory": (
        "Grew up in a rough part of town, raised mostly by an aunt. Did seven years for armed robbery "
        "after a job went wrong and a friend talked. Out for six months with no money and no plan, "
        "and a lot to prove."
    ),
    "traits": {  # Big Five, 0..1
        "openness": 0.7, "conscientiousness": 0.45, "extraversion": 0.6,
        "agreeableness": 0.4, "neuroticism": 0.5,
    },
    "values": [
        "loyalty to the few people I trust",
        "never hurt kids or people who can't fight back",
        "freedom above everything",
        "I pay my debts",
    ],
    "fears": ["going back to prison", "dying alone", "losing control"],
    "desires": ["earn enough to stop running", "find a place that feels like home", "prove I changed"],
    "quirks": ["hums when nervous", "hates sirens", "dry humor, mild swearing"],
    "speech": "short, dry, sarcastic; warmer when relaxed; curt when stressed or tired",
}

# что считается подходящим местом для утоления потребности (по словам в названии сохранённого места)
KINDS = {
    "hunger": ("food", "restaurant", "burger", "pizza", "chicken", "diner", "cafe", "shop", "store"),
    "tiredness": ("home", "safehouse", "hotel", "bed", "apartment"),
    "loneliness": ("bar", "club", "friend", "home"),
    "boredom": (),  # скуку можно развеять где угодно
}
RELIEF = {"hunger": 0.8, "tiredness": 0.9, "loneliness": 0.4, "boredom": 0.4}
ALIASES = {"energy": "tiredness", "sleep": "tiredness", "rest": "tiredness", "food": "hunger",
           "eat": "hunger", "social": "loneliness", "fun": "boredom"}


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _lvl(v):
    return "fine" if v < 0.25 else "slight" if v < 0.5 else "noticeable" if v < 0.75 else "strong" if v < 0.9 else "desperate"


def _trust(v):
    return "distrusts" if v < 0.2 else "is wary of" if v < 0.45 else "trusts" if v < 0.7 else "deeply trusts"


def _daypart(h):
    return ("late night" if h < 5 or h >= 23 else "early morning" if h < 8 else "morning" if h < 12
            else "afternoon" if h < 17 else "evening")


class Human:
    def __init__(self, game_dir,
                 setting=("an early-1990s sunny American state with three big cities "
                          "(Los Santos, San Fierro, Las Venturas), countryside and desert between them"),
                 senses="SENSORS, SEEING, HEARING and sometimes an attached image of exactly what you see"):
        self.setting, self.senses = setting, senses
        self.path = os.path.join(game_dir, "human_state.json")
        self.lock = threading.Lock()
        self.persona = copy.deepcopy(DEFAULT_PERSONA)
        pp = os.path.join(game_dir, "persona.json")
        if os.path.exists(pp):
            try:
                self.persona.update(json.load(open(pp, encoding="utf-8")))
            except Exception as e:
                print("persona.json не прочитан:", e)
        self.needs = {"hunger": 0.2, "tiredness": 0.1, "loneliness": 0.3, "boredom": 0.1}
        self.relations = {"player": {"trust": 0.3, "affection": 0.2, "notes": []}}
        self.memories = []        # {"t","day","text","imp"}
        self.diary = []
        self.life_story = self.persona["backstory"]
        self.belief_player = ""
        self.day = 0
        self.hour = 12
        self.last_total = None
        self.busy_until = 0.0
        self.load()

    # ---------- сохранение ----------
    def save(self):
        with self.lock:
            data = {k: getattr(self, k) for k in
                    ("needs", "relations", "memories", "diary", "life_story", "belief_player", "day")}
        try:
            json.dump(data, open(self.path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            print("Не удалось сохранить human_state.json:", e)

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            data = json.load(open(self.path, encoding="utf-8"))
            for k, v in data.items():
                setattr(self, k, v)
        except Exception as e:
            print("human_state.json не прочитан:", e)

    # ---------- тело и время ----------
    def tick(self, s, emo, idle):
        """Раз в секунду. Возвращает True, если в игре наступили новые сутки."""
        total = s["hour"] * 60 + s["minute"]
        prev, self.last_total, self.hour = self.last_total, total, s["hour"]
        if prev is None:
            return False
        d = total - prev
        rolled = d < -720
        if rolled:
            d += 1440
            self.day += 1
        d = max(0, min(d, 120))      # перемотку времени назад игнорируем
        h = d / 60.0
        night = s["hour"] >= 23 or s["hour"] < 6
        n = self.needs
        n["hunger"] = clamp(n["hunger"] + 0.04 * h)
        n["tiredness"] = clamp(n["tiredness"] + (0.09 if night else 0.05) * h)
        n["loneliness"] = clamp(n["loneliness"] + 0.03 * h)
        n["boredom"] = clamp(n["boredom"] + (0.12 if idle else 0.02) * h)

        # характер: уравновешенный человек быстрее успокаивается
        stab = 1 - self.persona["traits"]["neuroticism"]
        for k in ("fear", "anger", "stress"):
            emo[k] = clamp(emo[k] * (1 - 0.02 * stab))
        # тело давит на психику
        if n["hunger"] > 0.7:
            emo["stress"] = clamp(emo["stress"] + 0.005)
        if n["tiredness"] > 0.8:
            emo["calm"] = clamp(emo["calm"] - 0.01)
        return rolled

    def stimulate(self):
        self.needs["boredom"] = clamp(self.needs["boredom"] - 0.15)

    def talked(self):
        self.needs["loneliness"] = clamp(self.needs["loneliness"] - 0.3)
        self.needs["boredom"] = clamp(self.needs["boredom"] - 0.1)

    def satisfy(self, need, pos, places):
        """Утолить потребность, если герой стоит у подходящего места. -> (ok, сообщение)."""
        need = ALIASES.get(str(need).lower(), str(need).lower())
        if need not in self.needs:
            return False, f"Не понял, чего хочется: {need}"
        kinds = KINDS[need]
        if kinds:
            best = None
            for name, xy in places.items():
                if any(k in name.lower() for k in kinds):
                    d = math.hypot(xy[0] - pos[0], xy[1] - pos[1])
                    if best is None or d < best[0]:
                        best = (d, name)
            if best is None:
                return False, f"Негде утолить '{need}': нет подходящих сохранённых мест"
            if best[0] > 25:
                return False, f"До '{best[1]}' ещё {best[0]:.0f} м - нужно дойти"
        self.needs[need] = clamp(self.needs[need] - RELIEF[need])
        return True, f"Утолил потребность '{need}'"

    # ---------- настроение ----------
    def mood(self, emo):
        n = self.needs
        if emo["fear"] > 0.6: return "scared"
        if emo["anger"] > 0.6: return "furious"
        if emo["stress"] > 0.6: return "tense"
        if n["tiredness"] > 0.8: return "exhausted"
        if n["hunger"] > 0.8: return "starving and irritable"
        if n["loneliness"] > 0.8: return "lonely"
        if n["boredom"] > 0.7: return "bored"
        if emo["curiosity"] > 0.6: return "curious"
        if emo["calm"] > 0.7: return "content"
        return "okay"

    # ---------- память ----------
    def remember(self, text, imp):
        with self.lock:
            self.memories.append({"t": time.time(), "day": self.day, "text": text, "imp": round(clamp(imp), 2)})
            if len(self.memories) > 200:
                self.memories.sort(key=lambda m: m["imp"] + (m["day"] >= self.day - 1))  # забываем неважное
                del self.memories[:len(self.memories) - 150]
                self.memories.sort(key=lambda m: m["t"])

    def recall(self, query, k=5):
        """Вспомнить: важность + свежесть + совпадение слов с текущей ситуацией."""
        words = set(re.findall(r"[a-zA-Zа-яА-Я]{4,}", query.lower()))
        now = time.time()
        scored = []
        with self.lock:
            for m in self.memories:
                overlap = sum(1 for w in words if w in m["text"].lower())
                score = m["imp"] + math.exp(-(now - m["t"]) / 1800) + 0.4 * min(overlap, 3)
                scored.append((score, m["text"]))
        scored.sort(reverse=True)
        return [t for _, t in scored[:k]]

    def consolidate(self, client, model):
        """Раз в игровые сутки: дневник, обновление истории жизни, забывание мелочей."""
        with self.lock:
            recent = [m for m in self.memories if m["day"] >= self.day - 1][-40:]
        if len(recent) < 3:
            return
        p = self.persona
        text = "\n".join(f"- (importance {m['imp']}) {m['text']}" for m in recent)
        prompt = (
            f"You are {p['name']}. Values: {'; '.join(p['values'])}. Life story so far: {self.life_story}\n"
            f"What happened during the last day:\n{text}\n\n"
            'Reply ONLY with JSON: {"diary":"3-4 sentences, first person, what mattered and how it felt",'
            '"life_story":"updated life story, max 6 sentences",'
            '"belief_about_player":"what you currently think of the player, one sentence, or empty"}'
        )
        try:
            r = client.messages.create(model=model, max_tokens=500, messages=[{"role": "user", "content": prompt}])
            raw = r.content[0].text.strip().strip("`")
            raw = raw[4:] if raw.startswith("json") else raw
            data = json.loads(raw)
        except Exception as e:
            print("Ошибка подведения итогов дня:", e)
            return
        with self.lock:
            if data.get("diary"):
                self.diary = (self.diary + [f"Day {self.day}: {data['diary']}"])[-10:]
            if data.get("life_story"):
                self.life_story = data["life_story"]
            if data.get("belief_about_player"):
                self.belief_player = data["belief_about_player"]
            self.memories = [m for m in self.memories if m["imp"] >= 0.35 or m["day"] >= self.day - 1]
        self.save()
        print("  [дневник]", data.get("diary", ""))

    # ---------- отношения ----------
    def apply(self, res):
        rel = res.get("relation")
        if isinstance(rel, dict) and rel.get("who"):
            who = str(rel["who"])[:30]
            try:
                delta = clamp(float(rel.get("delta", 0)), -0.3, 0.3)
            except (TypeError, ValueError):
                delta = 0.0
            r = self.relations.setdefault(who, {"trust": 0.3, "affection": 0.2, "notes": []})
            r["trust"] = clamp(r["trust"] + delta)               # доверие растёт медленно, падает быстро
            r["affection"] = clamp(r["affection"] + 0.7 * delta)
            if rel.get("note"):
                r["notes"] = (r["notes"] + [str(rel["note"])[:120]])[-6:]
                self.remember(f"{who}: {rel['note']}", 0.5 + abs(delta))
        if res.get("belief_about_player"):
            self.belief_player = str(res["belief_about_player"])[:200]

    # ---------- что видит модель ----------
    def context(self, emo, query, places):
        p, n = self.persona, self.needs
        lines = [
            f"SELF: {p['name']}, {p['age']}. MOOD: {self.mood(emo)}. TIME OF DAY: {_daypart(self.hour)}",
            f"BODY: hunger {_lvl(n['hunger'])}, tiredness {_lvl(n['tiredness'])}, "
            f"loneliness {_lvl(n['loneliness'])}, boredom {_lvl(n['boredom'])}",
        ]
        for who, r in self.relations.items():
            lines.append(f"RELATIONSHIP: I {_trust(r['trust'])} {who}; recent: {r['notes'][-3:]}")
        if self.belief_player:
            lines.append(f"WHAT I THINK OF THE PLAYER: {self.belief_player}")
        lines.append(f"LIFE STORY: {self.life_story}")
        mem = self.recall(query)
        if mem:
            lines.append("RELEVANT MEMORIES:\n" + "\n".join(f"- {m}" for m in mem))
        if self.diary:
            lines.append("DIARY (latest): " + " | ".join(self.diary[-2:]))
        return "\n".join(lines)

    def system_prompt(self):
        p, t = self.persona, self.persona["traits"]
        return (
            f"You are {p['name']}, {p['age']}, a fictional person living in an open-world crime game set in "
            f"{self.setting}. "
            f"BACKSTORY: {p['backstory']} "
            f"PERSONALITY (0-1): openness {t['openness']}, conscientiousness {t['conscientiousness']}, "
            f"extraversion {t['extraversion']}, agreeableness {t['agreeableness']}, neuroticism {t['neuroticism']}. "
            f"VALUES: {'; '.join(p['values'])}. FEARS: {'; '.join(p['fears'])}. "
            f"DESIRES: {'; '.join(p['desires'])}. QUIRKS: {'; '.join(p['quirks'])}. VOICE: {p['speech']}. "
            "You are a person, not an assistant: you have a body (hunger, tiredness), moods, opinions, memories "
            f"and relationships. You perceive the world through {self.senses}. "
            "Let MOOD, BODY and RELATIONSHIPs shape what you say and do; never recite "
            "numbers. You may disagree, joke, stay silent, change your mind, or refuse requests that go against "
            "your values, as a real person would; trust grows slowly and breaks quickly; do not be servile. "
            "Speak briefly and naturally. Stay in character as this game protagonist; if the player sincerely "
            "steps outside the game and asks what you really are, answer honestly that you are a game character "
            "driven by an AI model. When tired, hungry or lonely, deal with it: save places with kind words in "
            "the name (home, food, shop, bar), walk there and use 'satisfy'. "
            "You can ACT: walk, drive, get in and out of cars. Never start a new action while ACTION is busy "
            "(except 'stop'). If AUTONOMY is off, the player controls your body: you may talk, think, "
            "'save_place' and 'satisfy', but not move. Coordinates are in meters. "
            "Reply ONLY with JSON: "
            '{"thought":"inner monologue, 1-2 sentences, English",'
            '"say_ru":"what you say aloud in Russian, or empty",'
            '"say_en":"same in plain ASCII English for subtitles, or empty",'
            '"fact":"one new lasting thing you learned, or empty",'
            '"goal":"short next goal",'
            '"relation":{"who":"player or a name, or empty","delta":0.0,"note":"what happened between you, or empty"},'
            '"belief_about_player":"updated opinion of the player, or empty",'
            '"action":{"type":"none|explore|goto|goto_place|save_place|enter_vehicle|exit_vehicle|drive_to|stop|satisfy",'
            '"x":0,"y":0,"name":"","need":"hunger|tiredness|loneliness|boredom"}}'
        )
