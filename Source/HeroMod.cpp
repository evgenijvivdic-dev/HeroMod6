// HeroMod.cpp - ОДИН плагин для Vice City: герой (чувства + руки + субтитры) и "свободный мир".
//  * state.json    <- плагин пишет состояние мира и статус текущей команды
//  * commands.json -> плагин читает команды от Python (по полю "id")
//  * answer.txt    -> субтитры
//  * F10 в игре    -> аварийная остановка: герой бросает команду, управление возвращается игроку
//  Свободный мир: F5 розыск=0 | F6/F7 плотность улиц | F8 быстрое время | F3 запомнить точку, F4 вернуться
//  Сборка: проект Plugin-SDK для Vice City (Win32, Release) -> HeroMod.dll -> переименовать в HeroMod.asi
//
// Plugin-SDK (VC). Имена полей/перечислений помечены // VERIFY: они могут отличаться
// в вашей версии SDK - сверьте с заголовками (CPed.h, CVehicle.h, CAutoPilot.h, CPad.h).
#include "plugin.h"
#include "CMessages.h"
#include "CWorld.h"
#include "CPools.h"
#include "CClock.h"
#include "CWeather.h"
#include "CPad.h"
#include "CPopulation.h"
#include "CCarCtrl.h"
#include <algorithm>
#include <fstream>
#include <sstream>
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cmath>
using namespace plugin;

// ---------- мини-парсер JSON (ключи/значения простые) ----------
static std::string Slurp(const char *path) {
    std::ifstream f(path);
    if (!f) return "";
    return std::string((std::istreambuf_iterator<char>(f)), {});
}
static bool GetNum(const std::string &s, const char *key, float &out) {
    std::string k = std::string("\"") + key + "\":";
    size_t p = s.find(k);
    if (p == std::string::npos) return false;
    out = (float)atof(s.c_str() + p + k.size());
    return true;
}
static std::string GetStr(const std::string &s, const char *key) {
    std::string k = std::string("\"") + key + "\":\"";
    size_t p = s.find(k);
    if (p == std::string::npos) return "";
    p += k.size();
    size_t e = s.find('"', p);
    return e == std::string::npos ? "" : s.substr(p, e - p);
}

// ---------- исполнитель команд ----------
enum class Cmd { None, Goto, EnterVehicle, ExitVehicle, DriveTo };

struct Executor {
    Cmd type = Cmd::None;
    int id = 0;               // id последней принятой команды
    std::string status = "idle"; // idle | busy | done | failed
    CVector target;
    CVehicle *veh = nullptr;
    DWORD t0 = 0;

    static void ReleaseControl() {
        CPad::GetPad(0)->DisablePlayerControls = false; // VERIFY
    }

    void Finish(const char *st) {
        status = st;
        type = Cmd::None;
        ReleaseControl();
    }

    void StopAll(CPlayerPed *p, const char *st) {
        p->ClearObjective();                              // VERIFY (или SetObjective(OBJECTIVE_NONE))
        if (p->m_bInVehicle && p->m_pMyVehicle) {
            CVehicle *v = p->m_pMyVehicle;
            v->m_autoPilot.m_nCarMission = MISSION_NONE;  // VERIFY
            v->m_status = STATUS_PLAYER;                  // VERIFY: вернуть машину игроку
        }
        Finish(st);
    }

    CVehicle *NearestFreeVehicle(const CVector &pos, float maxDist) {
        CVehicle *best = nullptr;
        float bd = maxDist;
        for (int i = 0; i < CPools::ms_pVehiclePool->m_nSize; i++) {
            CVehicle *v = CPools::ms_pVehiclePool->GetAt(i);
            if (!v || v->m_pDriver) continue;             // VERIFY: поле водителя
            float d = (v->GetPosition() - pos).Magnitude();
            if (d < bd) { bd = d; best = v; }
        }
        return best;
    }

    void Start(CPlayerPed *p, const std::string &js) {
        std::string c = GetStr(js, "cmd");
        float x = 0, y = 0;
        GetNum(js, "x", x);
        GetNum(js, "y", y);
        t0 = GetTickCount();
        status = "busy";

        if (c == "stop") { StopAll(p, "done"); return; }

        if (c == "goto") {
            if (p->m_bInVehicle) { Finish("failed"); return; }
            target = CVector(x, y, CWorld::FindGroundZForCoord(x, y));
            p->SetObjective(OBJECTIVE_GOTO_AREA_ON_FOOT, target); // VERIFY
            p->SetMoveState(PEDMOVE_RUN);                          // VERIFY
            CPad::GetPad(0)->DisablePlayerControls = true;         // VERIFY
            type = Cmd::Goto;
        } else if (c == "enter_vehicle") {
            if (p->m_bInVehicle) { Finish("done"); return; }
            veh = NearestFreeVehicle(p->GetPosition(), 40.0f);
            if (!veh) { Finish("failed"); return; }
            p->SetObjective(OBJECTIVE_ENTER_CAR_AS_DRIVER, veh);   // VERIFY
            CPad::GetPad(0)->DisablePlayerControls = true;
            type = Cmd::EnterVehicle;
        } else if (c == "exit_vehicle") {
            if (!p->m_bInVehicle || !p->m_pMyVehicle) { Finish("done"); return; }
            p->SetObjective(OBJECTIVE_LEAVE_CAR, p->m_pMyVehicle); // VERIFY
            CPad::GetPad(0)->DisablePlayerControls = true;
            type = Cmd::ExitVehicle;
        } else if (c == "drive_to") {
            if (!p->m_bInVehicle || !p->m_pMyVehicle) { Finish("failed"); return; }
            veh = p->m_pMyVehicle;
            target = CVector(x, y, CWorld::FindGroundZForCoord(x, y));
            veh->m_status = STATUS_PHYSICS;                        // VERIFY: отдать машину автопилоту
            veh->m_autoPilot.m_nCarMission = MISSION_GOTOCOORDS;   // VERIFY
            veh->m_autoPilot.m_vecDestinationCoors = target;       // VERIFY
            veh->m_autoPilot.m_nCruiseSpeed = 20;                  // VERIFY
            veh->m_autoPilot.m_nCarDrivingStyle = DRIVINGSTYLE_AVOID_CARS; // VERIFY
            CPad::GetPad(0)->DisablePlayerControls = true;
            type = Cmd::DriveTo;
        } else {
            Finish("failed");
        }
    }

    void Tick(CPlayerPed *p) {
        if (type == Cmd::None) return;
        if (GetTickCount() - t0 > 90000) { StopAll(p, "failed"); return; } // таймаут 90 c
        CVector pos = p->GetPosition();
        switch (type) {
        case Cmd::Goto:
            if ((pos - target).Magnitude2D() < 2.5f) StopAll(p, "done");
            break;
        case Cmd::EnterVehicle:
            if (p->m_bInVehicle && p->m_pMyVehicle == veh) Finish("done");
            break;
        case Cmd::ExitVehicle:
            if (!p->m_bInVehicle) Finish("done");
            break;
        case Cmd::DriveTo:
            if (!p->m_bInVehicle || p->m_pMyVehicle != veh) StopAll(p, "failed");
            else if ((veh->GetPosition() - target).Magnitude2D() < 10.0f) StopAll(p, "done");
            break;
        default: break;
        }
    }
} exec;

// ---------- свободный мир ----------
static bool Pressed(int vk) {
    static bool held[256] = {};
    bool down = (GetAsyncKeyState(vk) & 0x8000) != 0;
    bool hit = down && !held[vk];
    held[vk] = down;
    return hit;
}
static void Say(const wchar_t *text) {
    static std::wstring buf;
    buf = text;
    CMessages::AddMessageJumpQ((wchar_t *)buf.c_str(), 2500, 0);   // VERIFY: в VC текст wchar_t*
}
static void OpenWorldTick(CPlayerPed *p) {
    static bool freeRoam = false, fastClock = false, hasSaved = false;
    static float density = 1.0f;
    static CVector saved;

    if (Pressed(VK_F5)) { freeRoam = !freeRoam; Say(freeRoam ? L"Free roam: ON" : L"Free roam: OFF"); }
    if (freeRoam && p->m_pWanted) {
        p->m_pWanted->m_nWantedLevel = 0;                 // VERIFY
        p->m_pWanted->m_nChaos = 0;                       // VERIFY
    }
    bool changed = false;
    if (Pressed(VK_F6)) { density = (std::min)(density + 0.25f, 2.0f); changed = true; }
    if (Pressed(VK_F7)) { density = (std::max)(density - 0.25f, 0.25f); changed = true; }
    CPopulation::PedDensityMultiplier = density;          // VERIFY
    CCarCtrl::CarDensityMultiplier = density;             // VERIFY
    if (changed) { wchar_t msg[32]; swprintf(msg, 32, L"Traffic density x%.2f", density); Say(msg); }

    if (Pressed(VK_F8)) { fastClock = !fastClock; Say(fastClock ? L"Fast clock: ON" : L"Fast clock: OFF"); }
    CClock::ms_nMillisecondsPerGameMinute = fastClock ? 250 : 1000;   // VERIFY

    if (Pressed(VK_F3)) { saved = p->GetPosition(); hasSaved = true; Say(L"Position saved"); }
    if (Pressed(VK_F4) && hasSaved) {
        CVector to = saved; to.z += 1.0f;
        p->Teleport(to);                                  // VERIFY: или SetPosition
        Say(L"Teleported");
    }
}

class HeroMod {
public:
    HeroMod() {
        Events::gameProcessEvent += [] {
            static DWORD lastState = 0, lastAns = 0, lastCmd = 0;
            DWORD now = GetTickCount();
            CPlayerPed *p = FindPlayerPed();
            if (!p) return;

            OpenWorldTick(p);   // свободный мир (до любых return ниже)

            // ---- аварийный выключатель: F10 ----
            if (GetAsyncKeyState(VK_F10) & 1) exec.StopAll(p, "failed");

            // ---- 1) команды от Python (3 раза в секунду) ----
            if (now - lastCmd >= 300) {
                lastCmd = now;
                std::string js = Slurp("commands.json");
                float id = 0;
                if (!js.empty() && GetNum(js, "id", id) && (int)id != exec.id) {
                    exec.id = (int)id;
                    exec.Start(p, js);
                }
            }
            exec.Tick(p);

            // ---- 2) сенсоры -> state.json (раз в секунду) ----
            if (now - lastState >= 1000) {
                lastState = now;
                CVector pos = p->GetPosition();
                int peds = 0, cars = 0;
                const float R = 40.0f;
                for (int i = 0; i < CPools::ms_pPedPool->m_nSize; i++) {
                    CPed *o = CPools::ms_pPedPool->GetAt(i);
                    if (o && o != p && (o->GetPosition() - pos).Magnitude() < R) peds++;
                }
                for (int i = 0; i < CPools::ms_pVehiclePool->m_nSize; i++) {
                    CVehicle *v = CPools::ms_pVehiclePool->GetAt(i);
                    if (v && (v->GetPosition() - pos).Magnitude() < R) cars++;
                }
                bool inCar = p->m_bInVehicle;
                float speed = inCar && p->m_pMyVehicle
                                ? p->m_pMyVehicle->m_vecMoveSpeed.Magnitude() * 180.0f : 0.0f;

                std::ostringstream js;
                js << "{"
                   << "\"x\":" << pos.x << ",\"y\":" << pos.y << ",\"z\":" << pos.z
                   << ",\"health\":" << p->m_fHealth
                   << ",\"armor\":" << p->m_fArmour
                   << ",\"money\":" << CWorld::Players[0].m_nMoney
                   << ",\"wanted\":" << p->m_pWanted->m_nWantedLevel
                   << ",\"in_vehicle\":" << (inCar ? "true" : "false")
                   << ",\"speed\":" << speed
                   << ",\"hour\":" << (int)CClock::ms_nGameClockHours
                   << ",\"minute\":" << (int)CClock::ms_nGameClockMinutes
                   << ",\"weather\":" << (int)CWeather::NewWeatherType
                   << ",\"peds_near\":" << peds
                   << ",\"cars_near\":" << cars
                   << ",\"cmd_id\":" << exec.id
                   << ",\"cmd_status\":\"" << exec.status << "\""
                   << "}";
                { std::ofstream f("state.tmp"); f << js.str(); }
                std::remove("state.json");
                std::rename("state.tmp", "state.json");
            }

            // ---- 3) субтитры ----
            if (now - lastAns >= 500) {
                lastAns = now;
                std::ifstream f("answer.txt");
                if (!f) return;
                std::string s((std::istreambuf_iterator<char>(f)), {});
                f.close();
                std::remove("answer.txt");
                if (s.empty()) return;
                std::wstring w(s.begin(), s.end());
                CMessages::AddMessageJumpQ((wchar_t *)w.c_str(), 6000, 0);
            }
        };
    }
} heroMod;
