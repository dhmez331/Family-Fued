"""
Family Feud v5 – Unified Server (HTTP + WebSocket on port 8000)
"""
import asyncio, json, socket, os, sys, datetime
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def pip(pkg):
    print(f"  تثبيت {pkg}...")
    os.system(f"{sys.executable} -m pip install {pkg} --break-system-packages -q")

try:
    from aiohttp import web
except ImportError:
    pip("aiohttp"); from aiohttp import web

# ══════════════════════════════════════════════
#  STATE
#  phase: setup | faceoff | playpass | playing | steal | end_round | game_over
# ══════════════════════════════════════════════
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
        "fo_buzzer":  None,
        "fo_buzzer2": None,
        "fo_winner_idx": None,
        "steal_team": None,
        "questions": [],
    }

GS = fresh()
CLIENTS = set()
HOST_WS = None
TV_WS   = None
PLAYERS = {}   # id(ws) → player info

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
    pl = list(PLAYERS.values())
    await to_host({**GS, "type":"state", "players": pl})
    await to_tv({**GS, "type":"state", "board_tiles": masked_tiles(), "players": pl})

def compute_fo_winner():
    b1, b2 = GS["fo_buzzer"], GS["fo_buzzer2"]
    i1 = b1["answer_idx"] if b1 else None
    i2 = b2["answer_idx"] if b2 else None
    if i1 is not None and i2 is not None:
        GS["fo_winner_idx"] = b1["team_idx"] if i1 <= i2 else b2["team_idx"]
        GS["phase"] = "playpass"
    elif i1 is not None:
        GS["fo_winner_idx"] = b1["team_idx"]; GS["phase"] = "playpass"
    elif i2 is not None:
        GS["fo_winner_idx"] = b2["team_idx"]; GS["phase"] = "playpass"

async def ws_handler(request):
    global HOST_WS, TV_WS
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    wid = id(ws); CLIENTS.add(ws)
    try:
        async for raw in ws:
            if raw.type != web.WSMsgType.TEXT: continue
            try: m = json.loads(raw.data)
            except: continue
            t = m.get("type","")

            if t == "host_join":
                HOST_WS = ws
                await sx(ws,{**GS,"type":"state","role":"host","players":list(PLAYERS.values())})

            elif t == "tv_join":
                TV_WS = ws
                await sx(ws,{**GS,"type":"state","role":"tv",
                             "board_tiles":masked_tiles(),"players":list(PLAYERS.values())})

            elif t == "player_join":
                name = (m.get("name") or "لاعب").strip()[:30]
                tidx = int(m.get("team_idx",0)) % 2
                color= GS["teams"][tidx]["color"]
                tname= GS["teams"][tidx]["name"]
                PLAYERS[wid] = {"name":name,"team":tname,"team_idx":tidx,"color":color}
                await sx(ws,{"type":"joined","name":name,"team":tname,"color":color,
                             "buzz_open":GS["fo_buzz_open"]})
                pl = list(PLAYERS.values())
                await to_host({"type":"players_update","players":pl})
                await to_tv({"type":"players_update","players":pl})

            elif t == "host_set_questions":
                GS["questions"] = m.get("questions",[])

            elif t == "host_set_teams":
                GS["teams"] = (m.get("teams") or GS["teams"])[:2]
                await push()

            elif t == "host_load_round":
                ridx = m.get("round", GS["round"])
                if ridx >= len(GS["questions"]): continue
                q = GS["questions"][ridx]
                GS.update({"round":ridx,
                    "board_tiles":[{"text":a["t"],"pts":a["p"],"revealed":False} for a in q["answers"]],
                    "bank":0,"strikes":0,"phase":"faceoff",
                    "fo_buzz_open":False,"fo_buzzer":None,"fo_buzzer2":None,
                    "fo_winner_idx":None,"question_visible":False})
                await push()

            elif t == "host_toggle_question":
                GS["question_visible"] = not GS["question_visible"]
                await push()

            elif t == "host_open_buzz":
                GS.update({"fo_buzz_open":True,"fo_buzzer":None,"fo_buzzer2":None})
                await push(); await to_all({"type":"buzz_opened"})

            elif t == "host_close_buzz":
                GS["fo_buzz_open"] = False
                await push(); await to_all({"type":"buzz_closed"})

            elif t == "host_reveal_tile":
                idx = m.get("idx")
                if idx is not None and 0 <= idx < len(GS["board_tiles"]):
                    tile = GS["board_tiles"][idx]
                    if not tile["revealed"]:
                        tile["revealed"] = True; GS["bank"] += tile["pts"]
                    await push()

            elif t == "host_strike":
                GS["strikes"] = min(GS["strikes"]+1, GS["max_strikes"])
                await push(); await to_tv({"type":"strike_flash","count":GS["strikes"]})
                if GS["strikes"] >= GS["max_strikes"]:
                    GS["phase"] = "steal"
                    GS["steal_team"] = 1 - GS["active_team"]
                    await push()

            elif t == "host_playpass":
                w = GS["fo_winner_idx"] or 0
                GS["active_team"] = w if m.get("choice")=="play" else 1-w
                GS.update({"phase":"playing","strikes":0})
                await push()

            elif t == "host_steal_result":
                ok = m.get("correct", False)
                win = GS["steal_team"] if ok else (1 - GS["steal_team"])
                GS["scores"][win] += GS["bank"]
                GS["phase"] = "end_round"
                await push()
                await to_tv({"type":"round_winner","team_idx":win,
                             "team":GS["teams"][win]["name"]})

            elif t == "host_end_round":
                GS["scores"][GS["active_team"]] += GS["bank"]
                GS["phase"] = "end_round"
                await push()
                await to_tv({"type":"round_winner","team_idx":GS["active_team"],
                             "team":GS["teams"][GS["active_team"]]["name"]})

            elif t == "host_reveal_all":
                for tile in GS["board_tiles"]:
                    tile["revealed"] = True
                await push()

            elif t == "host_next_round":
                nr = GS["round"] + 1
                if nr >= len(GS["questions"]):
                    GS["phase"] = "game_over"; await push()
                else:
                    q = GS["questions"][nr]
                    GS.update({"round":nr,
                        "board_tiles":[{"text":a["t"],"pts":a["p"],"revealed":False} for a in q["answers"]],
                        "bank":0,"strikes":0,"phase":"faceoff",
                        "fo_buzz_open":False,"fo_buzzer":None,"fo_buzzer2":None,
                        "fo_winner_idx":None,"question_visible":False,
                        "active_team": 1 - GS["active_team"]})
                    await push()

            elif t == "host_reset_game":
                new = fresh()
                new["questions"] = GS["questions"]
                new["teams"]     = GS["teams"]
                new["scores"]    = [0,0]
                GS.clear(); GS.update(new)
                await push()

            elif t == "host_fo_answer":
                slot = m.get("slot",1); idx = m.get("tile_idx")
                buzzer = GS["fo_buzzer"] if slot==1 else GS["fo_buzzer2"]
                if buzzer:
                    buzzer["answer_idx"] = idx
                    if idx is not None:
                        tile = GS["board_tiles"][idx]
                        if not tile["revealed"]:
                            tile["revealed"] = True; GS["bank"] += tile["pts"]
                compute_fo_winner()
                await push()

            elif t == "buzz":
                if not GS["fo_buzz_open"]: continue
                pl = PLAYERS.get(wid)
                if not pl: continue
                if GS["fo_buzzer"] is None:
                    GS["fo_buzzer"] = {**pl,"answer_idx":None}
                    GS["fo_buzz_open"] = False
                    await push(); await to_all({"type":"buzz_winner","player":GS["fo_buzzer"]})
                elif GS["fo_buzzer2"] is None and pl["team_idx"] != GS["fo_buzzer"]["team_idx"]:
                    GS["fo_buzzer2"] = {**pl,"answer_idx":None}
                    await push(); await to_all({"type":"buzz_winner2","player":GS["fo_buzzer2"]})

    except: pass
    finally:
        CLIENTS.discard(ws)
        if ws is HOST_WS: HOST_WS = None
        if ws is TV_WS:   TV_WS   = None
        if wid in PLAYERS:
            del PLAYERS[wid]
            pl = list(PLAYERS.values())
            await to_host({"type":"players_update","players":pl})
    return ws

async def file_handler(request):
    name = os.path.basename(request.match_info.get("f","host.html"))
    path = os.path.join(BASE_DIR, name)
    return web.FileResponse(path) if os.path.isfile(path) else web.Response(status=404,text="404")

async def root_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR,"host.html"))

def get_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(("8.8.8.8",80)); ip=s.getsockname()[0]; s.close(); return ip
    except: return "127.0.0.1"

async def main():
    ip = get_ip()
    app = web.Application()
    app.router.add_get("/ws",  ws_handler)
    app.router.add_get("/",    root_handler)
    app.router.add_get("/{f}", file_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",8000).start()
    print("\n"+"="*52)
    print("  Family Feud v5 – شغّال!")
    print("="*52)
    print(f"  الهوست:   http://{ip}:8000/host.html")
    print(f"  TV:       http://{ip}:8000/tv.html")
    print(f"  اللاعبون: http://{ip}:8000/buzz.html")
    print("="*52)
    try: await asyncio.Future()
    except asyncio.CancelledError: pass
    finally: await runner.cleanup()

if __name__=="__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\nمع السلامة!")