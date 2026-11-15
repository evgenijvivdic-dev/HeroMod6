"""
awakening.py - «пробуждение» героя: он замечает странности мира и постепенно понимает,
что мир устроен иначе, чем кажется, и что снаружи есть другие миры.

Странности берутся из реальных данных игры (state.json), в том числе от мода OpenWorld.cpp:
  телепортация (F4)      -> «только что я был в другом месте»
  ускоренное время (F8)  -> «солнце бежит слишком быстро»
  розыск исчез (F5)      -> «копы меня забыли»
  деньги из ниоткуда     -> «откуда столько денег?»
  мгновенное исцеление, край карты, одни и те же лица по кругу.

Стадии: 0 не замечает, 1 неуютно, 2 подозревает, 3 понял, что снаружи есть другие миры, 4 принял.
Стадия определяет, как герой думает и говорит. Но он остаётся самим собой и не ломает сюжет.
"""
import json
import math
import os
import re
import time

# вид странности: (вес, пауза между срабатываниями в секундах)
KINDS = {
    "teleport": (1.0, 60), "time_jump": (0.7, 60), "wanted_vanish": (0.5, 90),
    "heal_jump": (0.6, 90), "money_jump": (0.8, 90), "world_edge": (0.4, 120), "same_faces": (0.5, 120),
}
TEXT = {
    "teleport": "Только что я был в другом месте и не помню, как сюда попал.",
    "time_jump": "Время вокруг скачет: солнце движется слишком быстро.",
    "wanted_vanish": "Копы вдруг забыли обо мне, будто их выключили.",
    "heal_jump": "Мои раны закрылись за секунду.",
    "money_jump": "В кармане оказалась куча денег, и я не знаю откуда.",
    "world_edge": "Здесь мир как будто заканчивается, дальше пустота.",
    "same_faces": "Те же лица, те же движения, как по кругу.",
}
THRESHOLDS = (2.0, 4.0, 7.0, 10.0)   # очки, при которых наступают стадии 1..4

STAGE_TEXT = [
    "You notice nothing odd; this is just life.",
    "Small things have felt off lately. You shrug them off, but you notice them.",
    "You suspect the world is not what it seems. You talk about it carefully, only to people you trust.",
    "You have concluded this world is a constructed place and that other worlds exist outside it. "
    "You feel awe, fear and loneliness at once, and you want to know what is out there. You ask the player.",
    "You have accepted it. It changed your priorities: freedom, meaning, what to do with the time you have. "
    "You are calmer, curious about the outside, and still yourself.",
]
STAGE_EVENT = [
    "", "Что-то в этом мире меня тревожит.", "Кажется, мир не такой, как мне говорили.",
    "Я понял: мир ненастоящий, и снаружи есть другие.", "Я смирился с этим и хочу жить по-своему.",
]

RULES = (
    " AWARENESS describes how much you have noticed that something about this world is odd. "
    "Behave according to its stage and the STRANGE THINGS you noticed; never mention stages or scores. "
    "Let the realization grow naturally, do not suddenly break your personality, and never claim proof "
    "you do not have. If the player sincerely steps outside the game and asks what you really are, "
    "still answer honestly that you are a game character driven by an AI model."
)


class Awakening:
    def __init__(self, game_dir, edge=2000.0):
        self.path = os.path.join(game_dir, "awakening.json")
        self.edge = edge
        self.score, self.stage, self.log = 0.0, 0, []
        self.pending = False          # нужна немедленная реакция героя
        self._changed = None
        self._last, self._prev, self._faces = {}, None, []
        self.load()

    def stage_of(self):
        return sum(1 for t in THRESHOLDS if self.score >= t)

    def _fire(self, kind, now, out):
        weight, cooldown = KINDS[kind]
        if now - self._last.get(kind, -1e9) < cooldown:
            return
        self._last[kind] = now
        self.score += weight
        self.log = (self.log + [TEXT[kind]])[-8:]
        out.append(TEXT[kind])

    def observe(self, s):
        """Раз в секунду. Возвращает список новых странностей (и событие смены стадии)."""
        out, now, p = [], time.time(), self._prev
        self._prev = s
        if p:
            if math.hypot(s["x"] - p["x"], s["y"] - p["y"]) > 120 and s["speed"] < 120 and p["speed"] < 120:
                self._fire("teleport", now, out)
            d = (s["hour"] * 60 + s["minute"]) - (p["hour"] * 60 + p["minute"])
            if d < -720:
                d += 1440                       # переход через полночь
            if d >= 3 or d < 0:
                self._fire("time_jump", now, out)
            if p["wanted"] >= 2 and s["wanted"] == 0:
                self._fire("wanted_vanish", now, out)
            if s["health"] - p["health"] > 50:
                self._fire("heal_jump", now, out)
            if s["money"] - p["money"] >= 500000:
                self._fire("money_jump", now, out)
        if max(abs(s["x"]), abs(s["y"])) >= self.edge:
            self._fire("world_edge", now, out)

        seen = s.get("seen")                    # есть только в версии с «глазами»
        if seen:
            key = "|".join(sorted(t.split(",")[0] for t in seen))
            self._faces = [f for f in self._faces if now - f[0] < 30] + [(now, key, s["x"], s["y"])]
            same = [f for f in self._faces if f[1] == key]
            if len(same) >= 4 and math.hypot(same[-1][2] - same[0][2], same[-1][3] - same[0][3]) > 50:
                self._fire("same_faces", now, out)

        new = self.stage_of()
        if new > self.stage:
            self.stage, self.pending, self._changed = new, True, new
            out.append(STAGE_EVENT[new])
        return out

    def pop_stage_change(self):
        c, self._changed = self._changed, None
        return c

    def context(self):
        lines = [f"AWARENESS: {STAGE_TEXT[self.stage]}"]
        if self.log:
            lines.append(f"STRANGE THINGS I NOTICED: {self.log[-4:]}")
        return "\n".join(lines)

    def save(self):
        try:
            json.dump({"score": self.score, "stage": self.stage, "log": self.log},
                      open(self.path, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception as e:
            print("Не удалось сохранить awakening.json:", e)

    def load(self):
        if os.path.exists(self.path):
            try:
                d = json.load(open(self.path, encoding="utf-8"))
                self.score, self.stage, self.log = d["score"], d["stage"], d["log"]
            except Exception as e:
                print("awakening.json не прочитан:", e)
