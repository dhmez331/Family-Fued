"""
Family Feud v6 – Fixed Server
"""
import asyncio, json, socket, os, sys
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def pip(pkg):
    os.system(f"{sys.executable} -m pip install {pkg} --break-system-packages -q")

try:
    from aiohttp import web
except ImportError:
    pip("aiohttp"); from aiohttp import web

def fresh():
    return {
        "phase": "setup",
        "round": 0,
        "question_visible": False,
        "board_tiles": [],
        "bank": 0,
        "strikes": 0,
        "max_strikes": 3,
        "active_team": 0,
        "scores": [0, 0],
        "teams": [{"name":"الفريق الأزرق","color":"#2979FF"},
                  {"name":"الفريق الأحمر","color":"#E53935"}],
        "fo_buzz_open": False,
        "fo_buzzer": None,
        "fo_buzzer2": None,
        "fo_winner_idx": None,
        "steal_team": None,
        "questions": [],
        "theme": "classic",
    }

GS = fresh()
CLIENTS = set()
HOST_WS = None
TV_WS   = None
PLAYERS = {}

async def sx(ws, m):
    try: await ws.send_str(json.dumps(m, ensure_ascii=False))
    except: pass

async def to_host(m):
    if HOST_WS: await sx(HOST_WS, m)

async def to_tv(m):
    if TV_WS: await sx(TV_WS, m)

async def to_all(m):
    for w in list(CLIENTS): await sx(w, m)

def masked_tiles():
    return [{"text": t["text"] if t["revealed"] else "",
             "pts":  t["pts"]  if t["revealed"] else 0,
             "revealed": t["revealed"]}
            for t in GS["board_tiles"]]

async def push():
    """يرسل state للهوست (كامل) وللـ TV (مخفي)"""
    pl = list(PLAYERS.values())
    # للهوست: كل شيء مكشوف
    host_state = {**GS, "type": "state", "role": "host", "players": pl}
    await to_host(host_state)
    # للـ TV: الإجابات مخفية
    tv_state = {**GS, "type": "state", "role": "tv",
                "board_tiles": masked_tiles(), "players": pl}
    await to_tv(tv_state)

def compute_fo_winner():
    """
    منطق المواجهة:
    - لو B1 أجاب رقم 1 (idx=0) → playpass مباشرة لفريقه
    - لو B1 أجاب غير رقم 1 أو أخطأ → ابقَ في faceoff وانتظر B2
    - لو B1 وB2 أجابا → قارن: الأقل index (= الأعلى نقاطاً) يفوز
    - لو كلاهما أخطأ (idx=-1) → الهوست يختار يدوياً
    """
    b1, b2 = GS["fo_buzzer"], GS["fo_buzzer2"]
    i1 = b1["answer_idx"] if b1 and b1.get("answer_idx") is not None else None
    i2 = b2["answer_idx"] if b2 and b2.get("answer_idx") is not None else None

    # كلاهما أجابا → قارن
    if i1 is not None and i2 is not None:
        if i1 == -1 and i2 == -1:
            # كلاهما أخطأ → ابقَ في faceoff، الهوست يختار يدوياً
            pass
        elif i1 == -1:
            # B1 أخطأ → B2 يفوز
            GS["fo_winner_idx"] = b2["team_idx"]
            GS["phase"] = "playpass"
        elif i2 == -1:
            # B2 أخطأ → B1 يفوز
            GS["fo_winner_idx"] = b1["team_idx"]
            GS["phase"] = "playpass"
        else:
            # كلاهما صح → الأقل index (الأعلى في القائمة) يفوز
            GS["fo_winner_idx"] = b1["team_idx"] if i1 <= i2 else b2["team_idx"]
            GS["phase"] = "playpass"

    # B1 فقط أجاب
    elif i1 is not None:
        if i1 == 0:
            # رقم 1 مباشرة → playpass لفريقه
            GS["fo_winner_idx"] = b1["team_idx"]
            GS["phase"] = "playpass"
        else:
            # غير رقم 1 أو خطأ → ابقَ في faceoff وانتظر B2
            # (الهوست يفتح الزر للاعب الثاني من الـ host panel)
            pass

    # B2 فقط أجاب (نادر - لو B1 لم يُحدد)
    elif i2 is not None:
        GS["fo_winner_idx"] = b2["team_idx"]
        GS["phase"] = "playpass"

async def ws_handler(request):
    global HOST_WS, TV_WS
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    wid = id(ws)
    CLIENTS.add(ws)
    try:
        async for raw in ws:
            if raw.type != web.WSMsgType.TEXT: continue
            try: m = json.loads(raw.data)
            except: continue
            t = m.get("type", "")

            # ── تسجيل الدخول ──────────────────────────────
            if t == "host_join":
                HOST_WS = ws
                pl = list(PLAYERS.values())
                await sx(ws, {**GS, "type": "state", "role": "host", "players": pl})
                # اطلب من الهوست يرسل البيانات
                await sx(ws, {"type": "request_sync"})

            elif t == "tv_join":
                TV_WS = ws
                pl = list(PLAYERS.values())
                await sx(ws, {**GS, "type": "state", "role": "tv",
                              "board_tiles": masked_tiles(), "players": pl})

            elif t == "player_join":
                name  = (m.get("name") or "لاعب").strip()[:30]
                tidx  = int(m.get("team_idx", 0)) % len(GS["teams"])
                color = GS["teams"][tidx]["color"]
                tname = GS["teams"][tidx]["name"]
                PLAYERS[wid] = {"name": name, "team": tname, "team_idx": tidx, "color": color}
                print(f"[PLAYER_JOIN] name={name} team={tname} HOST_WS={'connected' if HOST_WS else 'NONE!'} clients={len(CLIENTS)}")
                # أخبر اللاعب بالانضمام
                await sx(ws, {"type": "joined", "name": name, "team": tname,
                              "color": color, "buzz_open": GS["fo_buzz_open"]})
                # أخبر الكل (broadcast) عشان نضمن وصول الرسالة
                pl = list(PLAYERS.values())
                await to_all({"type": "players_update", "players": pl})
                # أرسل state كامل للهوست والـ TV
                await push()

            # ── بيانات الهوست ─────────────────────────────
            elif t == "host_set_questions":
                GS["questions"] = m.get("questions", [])
                await push()

            elif t == "host_set_teams":
                teams = m.get("teams")
                if teams: GS["teams"] = teams[:2]
                await push()

            elif t == "host_set_theme":
                GS["theme"] = m.get("theme", "classic")
                # أرسل الثيم للـ TV و buzz مباشرة
                await to_tv({"type": "theme_change", "theme": GS["theme"]})
                await to_all({"type": "theme_change", "theme": GS["theme"]})

            # ── تحكم اللعبة ───────────────────────────────
            elif t == "host_load_round":
                ridx = m.get("round", GS["round"])
                if m.get("questions"):
                    GS["questions"] = m["questions"]
                if not GS["questions"] or ridx >= len(GS["questions"]):
                    continue
                q = GS["questions"][ridx]
                GS.update({
                    "round": ridx,
                    "board_tiles": [{"text": a["t"], "pts": a["p"], "revealed": False}
                                    for a in q["answers"]],
                    "bank": 0, "strikes": 0, "phase": "faceoff",
                    "fo_buzz_open": False, "fo_buzzer": None,
                    "fo_buzzer2": None, "fo_winner_idx": None,
                    "question_visible": False,
                })
                await push()

            elif t == "host_toggle_question":
                GS["question_visible"] = not GS["question_visible"]
                await push()

            elif t == "host_open_buzz":
                GS.update({"fo_buzz_open": True, "fo_buzzer": None, "fo_buzzer2": None})
                await push()
                await to_all({"type": "buzz_opened"})

            elif t == "host_close_buzz":
                GS["fo_buzz_open"] = False
                await push()
                await to_all({"type": "buzz_closed"})

            elif t == "host_reveal_tile":
                idx = m.get("idx")
                if idx is not None and 0 <= idx < len(GS["board_tiles"]):
                    tile = GS["board_tiles"][idx]
                    if not tile["revealed"]:
                        tile["revealed"] = True
                        GS["bank"] += tile["pts"]
                    await push()

            elif t == "host_strike":
                GS["strikes"] = min(GS["strikes"] + 1, GS["max_strikes"])
                await push()
                await to_tv({"type": "strike_flash", "count": GS["strikes"]})
                if GS["strikes"] >= GS["max_strikes"]:
                    GS["phase"] = "steal"
                    GS["steal_team"] = 1 - GS["active_team"]
                    await push()

            elif t == "host_playpass":
                w = GS["fo_winner_idx"] if GS["fo_winner_idx"] is not None else 0
                GS["active_team"] = w if m.get("choice") == "play" else 1 - w
                GS.update({"phase": "playing", "strikes": 0})
                await push()

            elif t == "host_steal_result":
                ok = m.get("correct", False)
                win = GS["steal_team"] if ok else (1 - GS["steal_team"])
                GS["scores"][win] += GS["bank"]
                GS["phase"] = "end_round"
                await push()
                await to_tv({"type": "round_winner", "team_idx": win,
                             "team": GS["teams"][win]["name"]})

            elif t == "host_end_round":
                GS["scores"][GS["active_team"]] += GS["bank"]
                GS["phase"] = "end_round"
                await push()
                await to_tv({"type": "round_winner",
                             "team_idx": GS["active_team"],
                             "team": GS["teams"][GS["active_team"]]["name"]})

            elif t == "host_reveal_all":
                for tile in GS["board_tiles"]:
                    tile["revealed"] = True
                await push()

            elif t == "host_next_round":
                nr = GS["round"] + 1
                if nr >= len(GS["questions"]):
                    GS["phase"] = "game_over"
                    await push()
                else:
                    q = GS["questions"][nr]
                    GS.update({
                        "round": nr,
                        "board_tiles": [{"text": a["t"], "pts": a["p"], "revealed": False}
                                        for a in q["answers"]],
                        "bank": 0, "strikes": 0, "phase": "faceoff",
                        "fo_buzz_open": False, "fo_buzzer": None,
                        "fo_buzzer2": None, "fo_winner_idx": None,
                        "question_visible": False,
                        "active_team": 1 - GS["active_team"],
                    })
                    await push()

            elif t == "host_reset_game":
                new = fresh()
                new["questions"] = GS["questions"]
                new["teams"]     = GS["teams"]
                new["theme"]     = GS.get("theme", "classic")
                GS.clear(); GS.update(new)
                await push()
                await to_all({"type": "game_reset"})

            elif t == "host_cancel_game":
                GS.update({
                    "phase": "setup", "board_tiles": [],
                    "bank": 0, "strikes": 0,
                    "fo_buzz_open": False, "fo_buzzer": None,
                    "fo_buzzer2": None, "fo_winner_idx": None,
                    "question_visible": False,
                })
                await push()
                await to_all({"type": "game_cancelled"})

            elif t == "host_fo_answer":
                slot = m.get("slot", 1)
                idx  = m.get("tile_idx")
                buzzer = GS["fo_buzzer"] if slot == 1 else GS["fo_buzzer2"]
                if buzzer:
                    buzzer["answer_idx"] = idx
                    if idx is not None and 0 <= idx < len(GS["board_tiles"]):
                        tile = GS["board_tiles"][idx]
                        if not tile["revealed"]:
                            tile["revealed"] = True
                            GS["bank"] += tile["pts"]
                compute_fo_winner()
                await push()

            # ── Ping (keepalive) ──────────────────────────
            elif t == "ping":
                pass  # تجاهل الـ ping

            elif t == "host_skip_fo":
                # الهوست يختار يدوياً أي فريق يلعب
                team_idx = m.get("team_idx", 0)
                GS["fo_winner_idx"] = team_idx
                GS["active_team"] = team_idx
                GS.update({"phase": "playing", "strikes": 0})
                await push()

            elif t == "get_players":
                pl = list(PLAYERS.values())
                await sx(ws, {"type": "players_update", "players": pl})

            # ── Buzz من اللاعبين ──────────────────────────
            elif t == "buzz":
                if not GS["fo_buzz_open"]: continue
                pl = PLAYERS.get(wid)
                if not pl: continue
                if GS["fo_buzzer"] is None:
                    GS["fo_buzzer"] = {**pl, "answer_idx": None}
                    GS["fo_buzz_open"] = False
                    await push()
                    await to_all({"type": "buzz_winner", "player": GS["fo_buzzer"]})
                elif (GS["fo_buzzer2"] is None and
                      pl["team_idx"] != GS["fo_buzzer"]["team_idx"]):
                    GS["fo_buzzer2"] = {**pl, "answer_idx": None}
                    await push()
                    await to_all({"type": "buzz_winner2", "player": GS["fo_buzzer2"]})

    except Exception as e:
        print(f"WS error: {e}")
    finally:
        CLIENTS.discard(ws)
        if ws is HOST_WS: HOST_WS = None
        if ws is TV_WS:   TV_WS   = None
        if wid in PLAYERS:
            del PLAYERS[wid]
            pl = list(PLAYERS.values())
            await to_host({"type": "players_update", "players": pl})
    return ws

async def file_handler(request):
    name = os.path.basename(request.match_info.get("f", "host.html"))
    path = os.path.join(BASE_DIR, name)
    if os.path.isfile(path):
        return web.FileResponse(path)
    return web.Response(status=404, text=f"404: {name}")

async def root_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, "host.html"))

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

async def main():
    PORT = int(os.environ.get("PORT", 8000))
    ip = get_ip()
    app = web.Application()
    app.router.add_get("/ws",   ws_handler)
    app.router.add_get("/",     root_handler)
    app.router.add_get("/{f}",  file_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"\n{'='*50}")
    print("  Family Feud v6 - شغّال!")
    print(f"{'='*50}")
    print(f"  الهوست:    http://{ip}:{PORT}/host.html")
    print(f"  TV:        http://{ip}:{PORT}/tv.html")
    print(f"  اللاعبون:  http://{ip}:{PORT}/buzz.html")
    print(f"{'='*50}")
    try: await asyncio.Future()
    except asyncio.CancelledError: pass
    finally: await runner.cleanup()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\nمع السلامة!")