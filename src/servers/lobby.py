from dotenv import load_dotenv
import os
import subprocess
import json
from utils.TCPutils import *
import threading
from typing import Optional, Dict, Any, Tuple
import socket
from DBclient import DatabaseClient
from time import sleep
import shutil
from get_game import get_game_location

# ==========================================
# 1. Operation Registry & Decorator
# ==========================================
OP_REGISTRY = {}

def handle_op(op_code: str, auth_required: bool = True):
    """
    Decorator to register request handlers.
    """
    def decorator(func):
        OP_REGISTRY[op_code] = {
            "func": func,
            "auth_required": auth_required
        }
        return func
    return decorator

class InvalidParameter(Exception):
    pass

class MultiThreadedServer:
    def __init__(self, host: str, port: int , db_host: str, db_port:int ):
        self.host = host
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.is_running = False

        # user_id -> socket
        self.client_sockets: Dict[Any, socket.socket] = {}
        # user_id -> boolean (True = someone is sending)
        self.sending_flag: Dict[Any, bool] = {}
        
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)

        self.db_host = db_host
        self.db_port = db_port

        # Storage paths
        self.storage_dir = "src/servers/uploaded_games"
        self.temp_dir = "src/servers/lobby_tmp"
        os.makedirs(self.temp_dir, exist_ok=True)

    def start(self):
        self.server_socket = create_tcp_passive_socket(self.host, self.port)
        self.is_running = True
        self.server_socket.settimeout(1.0)
        print(f"Server started on {self.host}:{self.port}")

        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()
        self._command_loop()
        accept_thread.join()
        print("Server fully stopped.")

    def _command_loop(self):
        while True:
            cmd = input("(input exit to stop the server)").strip()
            if cmd == "exit":
                print("Stopping server...")
                self.is_running = False
                self.server_socket.close()
                break

    def _accept_loop(self):
        while self.is_running:
            try:
                client_sock, addr = self.server_socket.accept()
            except socket.timeout:
                continue
            except OSError as e:
                print("Server socket closed:", e)
                break
            
            print(f"Accepted connection from {addr}")
            t = threading.Thread(target=self._client_handler, args=(addr, client_sock), daemon=True)
            t.start()
        print(f"accept loop exit")

    # -------------------------------------------------------
    # Async Send Logic
    # -------------------------------------------------------
    def send_to_client_async(self, user_id, message):
        t = threading.Thread(target=self._send_worker, args=(user_id, message), daemon=True)
        t.start()

    def _send_worker(self, user_id, message):
        with self.cond:
            if user_id not in self.sending_flag:
                return
            while self.sending_flag[user_id]:
                self.cond.wait()
            self.sending_flag[user_id] = True

        with self.lock:
            sock = self.client_sockets.get(user_id)

        if sock is None:
            with self.cond:
                if user_id in self.sending_flag:
                    self.sending_flag[user_id] = False
                    self.cond.notify_all()
            return

        try:
            send_json(sock, message)
        except Exception as e:
            pass

        with self.cond:
            if user_id in self.sending_flag:
                self.sending_flag[user_id] = False
                self.cond.notify_all()

    def _add_id_socket_mapping(self, user_id, client_sock):
        with self.lock:
            self.client_sockets[user_id] = client_sock
            self.sending_flag[user_id] = False

    # -------------------------------------------------------
    # Main Client Loop
    # -------------------------------------------------------
    def _client_handler(self, user_port, client_sock):
        print(f"[DEBUG] handler started for {user_port}")
        user_id = None
        db = DatabaseClient(self.db_host, self.db_port)
        
        with client_sock:
            while self.is_running:
                try:
                    msg, _ = recv_file(client_sock, "temp", timeout=20)
                    if msg is None:
                        continue
                except (ConnectionClosedByPeer, ConnectionResetError):
                    break

                print(f"[DEBUG] msg from {user_port}: {msg}")

                op = msg.get("op")
                if not op:
                    self._send_error(client_sock, user_id, "unknown", "Missing 'op' field")
                    continue

                handler_info = OP_REGISTRY.get(op)
                
                if not handler_info:
                    self._send_error(client_sock, user_id, op, f"Unknown op '{op}'")
                    continue

                if handler_info["auth_required"] and user_id is None:
                    self._send_error(client_sock, user_id, op, "Login required")
                    continue

                try:
                    func = handler_info["func"]
                    new_id, keep_connected = func(self, msg, user_id, client_sock, db)
                    
                    if new_id is not None:
                        user_id = new_id
                    
                    if not keep_connected:
                        break

                except Exception as e:
                    print(f"[Error] Handling op '{op}': {e}")
                    import traceback
                    traceback.print_exc()
                    self._send_error(client_sock, user_id, op, f"Internal server error: {str(e)}")

        # Cleanup
        db.close()
        if user_id is not None:
            with self.cond:
                while self.sending_flag.get(user_id, False):
                    self.cond.wait()
                if user_id in self.client_sockets:
                    del self.client_sockets[user_id]
                if user_id in self.sending_flag:
                    del self.sending_flag[user_id]
                self.cond.notify_all()
        print(f"Client {user_id} disconnected.")

    def _send_error(self, sock, user_id, op, error_msg):
        payload = {"status": "error", "op": op, "error": error_msg}
        if user_id is not None:
            self.send_to_client_async(user_id, payload)
        else:
            send_json(sock, payload)

    # ==========================================
    # Handlers
    # ==========================================

    @handle_op("print_sockets", auth_required=False)
    def _print_socket_map(self, msg, user_id, client_sock, db):
        with self.lock:
            print("Current client sockets:")
            for uid, sock in self.client_sockets.items():
                print(f"User ID: {uid}, Socket: {sock}")
        return user_id, True

    @handle_op("register", auth_required=False)
    def _register_user(self, msg, user_id, client_sock, db: DatabaseClient):
        name = msg.get("name")
        passwordHash = msg.get("passwordHash")
        if not name or not passwordHash:
            send_json(client_sock, {"status": "error", "op": "register", "error": "Missing fields"})
            return None, True

        try:
            exist = db.find_user_by_name_and_password(name, passwordHash)
            if exist:
                send_json(client_sock, {"status": "error", "op": "register", "error": "User already exists"})
                return None, True
            
            db.insert_user(name, passwordHash, 'player')
            get_id = db.find_user_by_name_and_password(name, passwordHash)
            new_id = get_id[0][0]
            
            send_json(client_sock, {"status": "ok", "op": "register", "id": new_id})
            self._add_id_socket_mapping(new_id, client_sock)
            return new_id, True
        except Exception as e:
            send_json(client_sock, {"status": "error", "op": "register", "error": str(e)})
            return None, True

    @handle_op("login", auth_required=False)
    def _login_user(self, msg, user_id, client_sock, db: DatabaseClient):
        name = msg.get("name")
        passwordHash = msg.get("passwordHash")
        if not name or not passwordHash:
            send_json(client_sock, {"status": "error", "op": "login", "error": "Missing fields"})
            return None, True

        try:
            user = db.find_user_by_name_and_password(name, passwordHash)
            if not user or user[0][4] != 'player':
                send_json(client_sock, {"status": "error", "op": "login", "error": "Invalid credentials"})
                return None, True
            
            new_id = user[0][0]
            db.update_user(new_id, status="online")
            
            self._add_id_socket_mapping(new_id, client_sock)
            send_json(client_sock, {"status": "ok", "op": "login", "id": new_id})
            return new_id, True
        except Exception as e:
            send_json(client_sock, {"status": "error", "op": "login", "error": str(e)})
            return None, True

    @handle_op("back", auth_required=False)
    def _back_to_lobby(self, msg, user_id, client_sock, db):
        req_id = int(msg.get("userId"))
        if not req_id:
            return None, True
        send_json(client_sock, {"op": "back", "status": "ok"})
        self._add_id_socket_mapping(req_id, client_sock)
        return req_id, True

    @handle_op("logout", auth_required=True)
    def _logout_user(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            in_room = db.check_user_in_room(user_id)
            if in_room:
                room_id = db.leave_room(user_id)
                users_in_room = db.list_user_in_room(room_id[0][0])
                if not users_in_room:
                    db.delete_room(room_id[0][0])
            
            db.delete_room_by_hostid(user_id)
            db.remove_invite_by_toid(user_id)
            db.remove_invite_by_fromid(user_id)
            db.remove_request_by_fromid(user_id)
            db.remove_request_by_toid(user_id)
            
            db.update_user(user_id, status="offline")
            return None, False
        except Exception as e:
            print(f"Logout error: {e}")
            return user_id, False

    @handle_op("list_rooms", auth_required=True)
    def _list_rooms(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            # list_all_rooms now returns [id, name, hostId, visibility, status, gameId, gameName]
            rooms = db.list_all_rooms()
            room_list = []
            for room in rooms:
                if room[3] == "private":
                    continue
                room_list.append({
                    "roomId": room[0],
                    "name": room[1],
                    "hostId": room[2],
                    "status": room[4],
                    "gameId": room[5],
                    "gameName": room[6]
                })
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_rooms", "rooms": room_list})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "list_rooms", "error": str(e)})
        return user_id, True

    @handle_op("list_online_users", auth_required=True)
    def _list_online_users(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            # list_online_users now filters out developers
            users = db.list_online_users()
            users_fmt = [{"id": u[0], "name": u[1]} for u in users]
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_online_users", "users": users_fmt})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "list_online_users", "error": str(e)})
        return user_id, True

    @handle_op("create_room", auth_required=True)
    def _create_room(self, msg, user_id, client_sock, db: DatabaseClient):
        name = msg.get("name")
        visibility = msg.get("visibility")
        gameId = msg.get("gameId")
        if not name or not visibility or not gameId:
            self.send_to_client_async(user_id, {"status": "error", "op": "create_room", "error": "Missing fields"})
            return user_id, True

        try:
            if db.check_user_in_room(user_id):
                self.send_to_client_async(user_id, {"status": "error", "op": "create_room", "error": "Already in room"})
                return user_id, True

            room_id = db.create_room(name, user_id, visibility, "idle", gameId)
            self.send_to_client_async(user_id, {"status": "ok", "op": "create_room", "room_id": room_id[0][0]})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "create_room", "error": str(e)})
        return user_id, True

    @handle_op("leave_room", auth_required=True)
    def _leave_room(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            if not db.check_user_in_room(user_id):
                self.send_to_client_async(user_id, {"status": "error", "op": "leave_room", "error": "Not in any room"})
                return user_id, True

            room_id = db.leave_room(user_id)
            users_in_room = db.list_user_in_room(room_id[0][0])
            if not users_in_room:
                db.delete_room(room_id[0][0])
            
            self.send_to_client_async(user_id, {"status": "ok", "op": "leave_room", "message": f"Left room {room_id[0][0]}"})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "leave_room", "error": str(e)})
        return user_id, True

    @handle_op("invite_user", auth_required=True)
    def _invite_user(self, msg, user_id, client_sock, db: DatabaseClient):
        invitee_id = int(msg.get("invitee_id"))
        if not invitee_id:
            return user_id, True
        
        try:
            if not db.find_user_by_id(invitee_id):
                self.send_to_client_async(user_id, {"status": "error", "op": "invite_user", "error": "Invitee not found"})
                return user_id, True
            
            room_id = db.check_user_in_room(user_id)
            if not room_id:
                self.send_to_client_async(user_id, {"status": "error", "op": "invite_user", "error": "You are not in a room"})
                return user_id, True

            invite_id = db.add_invite(room_id[0][0], invitee_id, user_id)
            sender = db.find_user_by_id(user_id)

            self.send_to_client_async(user_id, {"status": "ok", "op": "invite_user", "message": "Invited user"})
            self.send_to_client_async(invitee_id, {
                "status": "ok", 
                "op": "receive_invite", 
                "message": f"Invited by {sender[0][1]}",
                "roomId": room_id[0][0],
                "from_id": user_id,
                "invite_id": invite_id[0][0],
                "fromName": sender[0][1]
            })
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "invite_user", "error": str(e)})
        return user_id, True

    @handle_op("respond_invite", auth_required=True)
    def _respond_invite(self, msg, user_id, client_sock, db: DatabaseClient):
        response = msg.get("response")
        invite_id = int(msg.get("invite_id"))
        
        try:
            detail = db.get_invite_by_id(invite_id)
            if not detail or detail[0][3] != user_id:
                self.send_to_client_async(user_id, {"status": "error", "op": "respond_invite", "error": "Invalid invite"})
                return user_id, True

            room_id = detail[0][1]
            inviter_id = detail[0][2]

            if response == "accept":
                db.remove_invite_by_toid(user_id)
                db.remove_invite_by_fromid(user_id)
                db.add_user_to_room(room_id, user_id)
                
                self.send_to_client_async(user_id, {"status": "ok", "op": "respond_invite", "message": f"Joined room {room_id}" , "room_id": room_id} )
                self.send_to_client_async(inviter_id, {
                    "status": "ok", 
                    "op": "invite_accepted", 
                    "message": f"User {user_id} accepted invite", 
                    "roomId": room_id, 
                    "from_id": user_id
                })
            elif response == "decline":
                db.remove_invite_by_id(invite_id)
                self.send_to_client_async(user_id, {"status": "ok", "op": "respond_invite", "message": "Declined invite"})
                self.send_to_client_async(inviter_id, {
                    "status": "ok", 
                    "op": "invite_declined", 
                    "message": f"User {user_id} declined invite", 
                    "roomId": room_id, 
                    "from_id": user_id
                })
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "respond_invite", "error": str(e)})
        return user_id, True

    @handle_op("list_invite", auth_required=True)
    def _list_invitation(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            invites = db.list_invites(user_id)
            invite_list = [{
                "roomId": i[0], 
                "fromId": i[1], 
                "fromName": i[2], 
                "invite_id": i[3],
                "roomName": i[4],
                "gameId": i[5],
                "gameName": i[6]
            } for i in invites]
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_invite", "invites": invite_list})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "list_invite", "error": str(e)})
        return user_id, True

    @handle_op("request", auth_required=True)
    def _request(self, msg, user_id, client_sock, db: DatabaseClient):
        room_id = int(msg.get("room_id"))
        try:
            room = db.get_room_by_id(room_id, "public")
            if not room:
                self.send_to_client_async(user_id, {"status": "error", "op": "request", "error": "Room not found"})
                return user_id, True
            
            roomhost = int(room[0][2])
            req_id = db.insert_request(room_id, roomhost, user_id)
            
            self.send_to_client_async(user_id, {"status": "ok", "op": "request", "message": "Sending join request"})
            self.send_to_client_async(roomhost, {
                "status": "ok", 
                "op": "receive_request", 
                "message": f"User {user_id} requests to join",
                "room_id": room_id, 
                "from_id": user_id, 
                "request_id": req_id[0][0]
            })
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "request", "error": str(e)})
        return user_id, True

    @handle_op("respond_request", auth_required=True)
    def _respond_request(self, msg, user_id, client_sock, db: DatabaseClient):
        req_id = int(msg.get("request_id"))
        response = msg.get("response")
        try:
            detail = db.get_request_by_id(req_id, user_id)
            if not detail:
                self.send_to_client_async(user_id, {"status": "error", "op": "respond_request", "error": "Request not found"})
                return user_id, True

            room_id = int(detail[0][1])
            requester_id = int(detail[0][2])

            if response == "accept":
                db.remove_request_by_userid(requester_id)
                db.add_user_to_room(room_id, requester_id)
                
                self.send_to_client_async(user_id, {"status": "ok", "op": "respond_request", "respond": "accept", "message": "User added"})
                self.send_to_client_async(requester_id, {"status": "ok", "op": "request_accepted", "respond": "accept", "message": "Request accepted", "roomId": room_id})
            elif response == "decline":
                db.remove_request_by_id(req_id)
                self.send_to_client_async(user_id, {"status": "ok", "op": "respond_request", "respond": "declined", "message": "Declined"})
                self.send_to_client_async(requester_id, {"status": "ok", "op": "request_declined", "respond": "declined", "message": "Request declined", "roomId": room_id})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "respond_request", "error": str(e)})
        return user_id, True

    @handle_op("list_request", auth_required=True)
    def _list_request(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            requests = db.list_requests(user_id)
            req_list = [{"roomId": r[0], "fromId": r[1], "fromName": r[2], "request_id": r[3]} for r in requests]
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_request", "requests": req_list})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "list_request", "error": str(e)})
        return user_id, True

    @handle_op("list_games", auth_required=True)
    def _list_games(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            games = db.list_all_games()
            game_list = []
            if games:
                for g in games:
                    game_list.append({"game_id": g[0], "name": g[1]})
            
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_games", "games": game_list})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "list_games", "error": str(e)})
        return user_id, True

    @handle_op("show_game_data", auth_required=True)
    def _show_game_data(self, msg, user_id, client_sock, db: DatabaseClient):
        target_id = msg.get("game_id")
        if not target_id:
            self.send_to_client_async(user_id, {"status": "error", "op": "show_game_data", "error": "Missing 'game_id'"})
            return user_id, True

        try:
            game_data = db.get_game_by_id(target_id)
            if not game_data:
                self.send_to_client_async(user_id, {"status": "error", "op": "show_game_data", "error": "Game not found"})
            else:
                row = game_data[0]
                response_data = {
                    "id": row[0], "name": row[1], "description": row[2], 
                    "owner_id": row[3], "latest_version": row[4]
                }
                self.send_to_client_async(user_id, {"status": "ok", "op": "show_game_data", "data": response_data})
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "show_game_data", "error": str(e)})
        return user_id, True

    @handle_op("show_comment", auth_required=True)
    def _show_comment(self, msg, user_id, client_sock, db: DatabaseClient):
        target_id = msg.get("game_id")
        if not target_id:
            self.send_to_client_async(user_id, {
                "status": "error", 
                "op": "show_comment", 
                "error": "Missing 'game_id'"
            })
            return user_id, True

        try:
            # 1. Get Comments (using the JOIN query from DBclient)
            comments = db.get_comments_by_game_id(target_id)
            comment_list = []
            if comments:
                for c in comments:
                    comment_list.append({
                        "comment_id": c[0], 
                        "user_name": c[1], # Now guaranteed to be the name
                        "content": c[2], 
                        "score": c[3], 
                        "timestamp": c[4]
                    })
            
            # 2. Get Average Score (NEW)
            avg_score = db.get_average_score(target_id)

            # 3. Send combined response
            self.send_to_client_async(user_id, {
                "status": "ok", 
                "op": "show_comment", 
                "comments": comment_list,
                "average_score": round(avg_score[0][0], 1) # Rounded for cleaner UI
            })

        except Exception as e:
            self.send_to_client_async(user_id, {
                "status": "error", 
                "op": "show_comment", 
                "error": str(e)
            })
        
        return user_id, True
    @handle_op("download_game", auth_required=True)
    def _download_game(self, msg, user_id, client_sock, db: DatabaseClient):
        game_name = msg.get("game_name")
        if not game_name:
            self.send_to_client_async(user_id, {"status": "error", "op": "download_game", "error": "Missing game_name"})
            return user_id, True

        try:
            game_rows = db.get_game_by_name(game_name)
            if not game_rows:
                self.send_to_client_async(user_id, {"status": "error", "op": "download_game", "error": "Game not found"})
                return user_id, True
            
            owner_id = game_rows[0][3]
            latest_version = game_rows[0][4]
        except Exception as e:
            self.send_to_client_async(user_id, {"status": "error", "op": "download_game", "error": f"DB Error: {e}"})
            return user_id, True

        source_path = get_game_location(self.storage_dir, owner_id, game_name, latest_version)
        if not os.path.exists(source_path):
             self.send_to_client_async(user_id, {"status": "error", "op": "download_game", "error": "Game files missing on server"})
             return user_id, True

        # 3. Staging for Zip
        staging_dir = os.path.join(self.temp_dir, f"stage_{user_id}_{game_name}")
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir)
        os.makedirs(staging_dir)

        try:
            # --- COPY CLIENT FOLDER ---
            client_src = os.path.join(source_path, "client")
            if os.path.exists(client_src):
                shutil.copytree(client_src, os.path.join(staging_dir, "client"))
            
            # --- COPY CONFIG ---
            config_src = os.path.join(source_path, "config.json")
            if os.path.exists(config_src):
                shutil.copy(config_src, staging_dir)

            # --- NEW: COPY DEPENDENCY FILES ---
            # These are required for 'uv run' to work on the client side
            toml_src = os.path.join(source_path, "pyproject.toml")
            if os.path.exists(toml_src):
                shutil.copy(toml_src, staging_dir)
            
            lock_src = os.path.join(source_path, "uv.lock")
            if os.path.exists(lock_src):
                shutil.copy(lock_src, staging_dir)
            # ----------------------------------

            # 4. Zip it
            zip_base_name = os.path.join(self.temp_dir, f"pkg_{user_id}_{game_name}")
            archive_path = shutil.make_archive(zip_base_name, 'zip', staging_dir)
            
            # 5. Send File (Thread-safe)
            with self.cond:
                while self.sending_flag.get(user_id, False):
                    self.cond.wait()
                self.sending_flag[user_id] = True

            try:
                metadata = {
                    "status": "ok",
                    "op": "download_game",
                    "game_name": game_name,
                    "version": latest_version
                }
                # Using your existing send_file utility
                send_file(client_sock, archive_path, metadata)
            except Exception as e:
                print(f"Error sending file: {e}")
            finally:
                with self.cond:
                    self.sending_flag[user_id] = False
                    self.cond.notify_all()

        except Exception as e:
            print(f"Error zipping/staging: {e}")
            self.send_to_client_async(user_id, {"status": "error", "op": "download_game", "error": str(e)})
        finally:
            # 6. Cleanup
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)
            if 'archive_path' in locals() and os.path.exists(archive_path):
                os.remove(archive_path)

        return user_id, True

    @handle_op("start", auth_required=True)
    def _start_game(self, msg, user_id, client_sock, db: DatabaseClient):
        print(f"[DEBUG] received start op from user {user_id}")
        try:
            room_data = db.check_user_in_room(user_id)
            if not room_data:
                self.send_to_client_async(user_id, {"status": "error", "op": "start", "error": "Not in room"})
                return user_id, True
            
            roomId = room_data[0][0]
            room_users = db.list_user_in_room(roomId)
            if len(room_users) < 2:
                self.send_to_client_async(user_id, {"status": "error", "op": "start", "error": "Not enough players"})
                return user_id, True
            # get game info
            room = db.get_room_by_id(roomId)
            gameid = room[0][5]
            game = db.get_game_by_id(gameid)
            ownerid = game[0][3]
            game_name = game[0][1]
            LatestVersion = game[0][4]
            gamefolder = get_game_location(self.storage_dir,ownerid,game_name,LatestVersion)
            path = os.getcwd()
            os.chdir(gamefolder)    
            print("[DEBUG] creating process")
            game_server_process = subprocess.Popen(
                ["uv","run","server/server_main.py"], 
                stdin=subprocess.PIPE, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            os.chdir(path)
            room_user_ids = [id for id,name in room_users]
            message_to_gameserver = {
                "ip_address":self.host,
                "users": len(room_users),
                "userIDs":room_user_ids
            }
            print("[DEBUG] message_to_gameserver")
            print(message_to_gameserver)
            input_string = json.dumps(message_to_gameserver) + "\n"
            game_server_process.stdin.write(input_string)
            game_server_process.stdin.flush()
            game_server_process.stdin.close()

            #wait for the data
            port_line = game_server_process.stdout.readline().strip()
            print(f"[DEBUG] Game server output: {port_line}")
            game_port = int(port_line.split()[-1])

            db.update_room(roomId, status="playing")

            for user in room_users:
                self.send_to_client_async(user[0], {
                    "status": "ok", 
                    "op": "start", 
                    "game_server_ip": self.host, 
                    "game_server_port": game_port,
                    "game_name":game_name
                })

            monitor_thread = threading.Thread(
                target=self._gameserver_monitor, 
                args=(game_server_process, roomId), 
                daemon=True
            )
            monitor_thread.start()
            
            sleep(0.05)
            return user_id, True 
            
        except Exception as e:
            print(f"Start game error: {e}")
            self.send_to_client_async(user_id, {"status": "error", "op": "start", "error": str(e)})
            return user_id, True

    def _gameserver_monitor(self, process: subprocess.Popen, room_id):
        db = DatabaseClient(self.db_host, self.db_port)
        
        stdout_data = ""
        stderr_data = ""

        try:
            # REPLACEMENT FOR communicate()
            # Since stdin is already closed and we read the port from stdout manually,
            # we cannot use communicate(). We must read the remaining logs manually.
            
            # Read whatever is left in stdout (blocks until process closes stdout)
            if process.stdout:
                stdout_data = process.stdout.read()
            
            # Read whatever is left in stderr
            if process.stderr:
                stderr_data = process.stderr.read()

            # Ensure the process has fully terminated
            process.wait()

        except Exception as e:
            print(f"[Monitor] Error reading logs: {e}")
            return

        # Log output
        if stdout_data:
            print(f"[DEBUG Game {room_id} STDOUT]:\n{stdout_data.strip()}")
        if stderr_data:
            print(f"[DEBUG Game {room_id} STDERR]:\n{stderr_data.strip()}")

        # Update DB
        try:
            db.update_room(room_id, status="idle")
        except Exception as e:
            print(f"Failed to update room status: {e}")
            
        # Cleanup
        # Note: process.stdout and process.stderr are typically closed automatically 
        # by the read() reaching EOF, but we can ensure they are closed here safely.
        if process.stdout: 
            try: process.stdout.close() 
            except: pass
        if process.stderr: 
            try: process.stderr.close() 
            except: pass
        # Stdin was already closed in _start_game, so we don't touch it.

        print(f"[Monitor] Game server for room {room_id} exited cleanly.")
        db.close()

    @handle_op("add_comment", auth_required=True)
    def _add_comment(self, msg, user_id, client_sock, db: DatabaseClient):
        game_id = msg.get("game_id")
        content = msg.get("content")
        score = msg.get("score")

        # 1. Basic Validation
        if not game_id or not content or score is None:
            self.send_to_client_async(user_id, {
                "status": "error", 
                "op": "add_comment", 
                "error": "Missing game_id, content, or score"
            })
            return user_id, True

        # 2. Score Validation (Must be 1-5 based on DB constraints)
        try:
            score = int(score)
            if score < 1 or score > 5:
                raise ValueError("Score must be between 1 and 5")
        except ValueError:
            self.send_to_client_async(user_id, {
                "status": "error", 
                "op": "add_comment", 
                "error": "Score must be an integer between 1 and 5"
            })
            return user_id, True

        try:
            # 3. Insert into DB
            db.insert_comment(game_id, user_id, content, score)
            
            self.send_to_client_async(user_id, {
                "status": "ok", 
                "op": "add_comment", 
                "message": "Comment added successfully"
            })
            
        except Exception as e:
            self.send_to_client_async(user_id, {
                "status": "error", 
                "op": "add_comment", 
                "error": str(e)
            })

        return user_id, True

if __name__ == "__main__":
    load_dotenv()
    db_port = int(os.getenv("DB_PORT", "16384"))
    db_host = os.getenv("DB_IP", "140.113.17.11")
    lobby_host = os.getenv("LOBBY_IP", "140.113.17.12")
    lobby_port = int(os.getenv("LOBBY_PORT","20012"))
    server = MultiThreadedServer(lobby_host, lobby_port, db_host, db_port)
    server.start()