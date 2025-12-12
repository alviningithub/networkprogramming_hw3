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
    # Main Client Loop (Refactored)
    # -------------------------------------------------------
    def _client_handler(self, user_port, client_sock):
        print(f"[DEBUG] handler started for {user_port}")
        user_id = None
        db = DatabaseClient(self.db_host, self.db_port)
        
        with client_sock:
            while self.is_running:
                try:
                    # Note: We ignore filepath as server doesn't process incoming files here
                    msg, _ = recv_file(client_sock, "temp", timeout=20)
                    if msg is None:
                        continue
                except (ConnectionClosedByPeer, ConnectionResetError):
                    break

                print(f"[DEBUG] msg from {user_port}: {msg}")

                op = msg.get("op")
                
                # 1. Validation
                if not op:
                    self._send_error(client_sock, user_id, "unknown", "Missing 'op' field")
                    continue

                handler_info = OP_REGISTRY.get(op)
                
                # 2. Unknown Operation
                if not handler_info:
                    self._send_error(client_sock, user_id, op, f"Unknown op '{op}'")
                    continue

                # 3. Authentication Check
                if handler_info["auth_required"] and user_id is None:
                    self._send_error(client_sock, user_id, op, "Login required")
                    continue

                # 4. Dispatch
                # Standard signature: (msg, user_id, client_sock, db)
                # Returns: (new_user_id, keep_connected)
                try:
                    func = handler_info["func"]
                    # Call the method bound to 'self'
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
    # Handlers (Decorated)
    # Return: (new_user_id, keep_connected)
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
        # 1. Leave room logic
        try:
            in_room = db.check_user_in_room(user_id)
            if in_room:
                room_id = db.leave_room(user_id)
                users_in_room = db.list_user_in_room(room_id[0][0])
                if not users_in_room:
                    db.delete_room(room_id[0][0])
            
            # 2. Cleanup invites/requests
            db.delete_room_by_hostid(user_id)
            db.remove_invite_by_toid(user_id)
            db.remove_invite_by_fromid(user_id)
            db.remove_request_by_fromid(user_id)
            db.remove_request_by_toid(user_id)
            
            # 3. Status update
            db.update_user(user_id, status="offline")
            return None, False  # Disconnect
        except Exception as e:
            print(f"Logout error: {e}")
            return user_id, False # Disconnect anyway

    @handle_op("list_rooms", auth_required=True)
    def _list_rooms(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
            rooms = db.list_all_rooms()
            room_list = []
            for room in rooms:
                if room[3] == "private":
                    continue
                room_list.append({
                    "roomId": room[0],
                    "name": room[1],
                    "hostId": room[2],
                    "status": room[4]
                })
            self.send_to_client_async(user_id, {"status": "ok", "op": "list_rooms", "rooms": room_list})
        except Exception as e:
            print(e)
        return user_id, True

    @handle_op("list_online_users", auth_required=True)
    def _list_online_users(self, msg, user_id, client_sock, db: DatabaseClient):
        try:
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
                
                self.send_to_client_async(user_id, {"status": "ok", "op": "respond_invite", "message": f"Joined room {room_id}"})
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
                "invite_id": i[3]
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

            print("[DEBUG] creating process")
            game_server_process = subprocess.Popen(
                ["python", "-u", "tetris_server.py"], 
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            
            # Get port
            port_line = game_server_process.stdout.readline().decode().strip()
            print(f"[DEBUG] Game server output: {port_line}")
            game_port = int(port_line.split()[-1])

            db.update_room(roomId, status="playing")
            gamelog = db.create_gamelog(roomId)

            for user in room_users:
                self.send_to_client_async(user[0], {
                    "status": "ok", 
                    "op": "start", 
                    "game_server_ip": self.host, 
                    "game_server_port": game_port
                })

            monitor_thread = threading.Thread(
                target=self._gameserver_monitor, 
                args=(game_server_process, roomId, gamelog[0][0]), 
                daemon=True
            )
            monitor_thread.start()
            
            # Important: sleep briefly to ensure message sends before disconnect
            sleep(0.05)
            return user_id, False # Disconnect to switch to game server
            
        except Exception as e:
            print(f"Start game error: {e}")
            self.send_to_client_async(user_id, {"status": "error", "op": "start", "error": str(e)})
            return user_id, True

    # -------------------------------------------------------
    # Monitors
    # -------------------------------------------------------
    def _gameserver_monitor(self, process:subprocess.Popen[bytes], room_id, gamelog_id):
        db = DatabaseClient(self.db_host, self.db_port)
        try:
            stdout_data, stderr_data = process.communicate()
        except Exception as e:
            print(f"[Monitor] Error: {e}")
            return

        try:
            db.update_room(room_id, status="idle")
        except Exception as e:
            print(f"Failed to update room status: {e}")

        for line in stdout_data.decode().splitlines():
            if not line.strip(): continue
            try:
                data = json.loads(line.strip())
                uid = int(data.get("userId"))
                score = int(data.get("score"))
                db.update_gamelog(gamelog_id, uid, score)
            except Exception:
                continue

        process.stdout.close()
        process.stderr.close()
        if process.stdin: process.stdin.close()
        print(f"[Monitor] Game server for room {room_id} exited cleanly.")
        db.close()

if __name__ == "__main__":
    load_dotenv()
    db_port = int(os.getenv("DB_PORT", 20000))
    db_host = os.getenv("DB_IP", "127.0.0.1")
    lobby_host = os.getenv("LOBBY_IP", "127.0.0.1")
    server = MultiThreadedServer(lobby_host, 20012, db_host, db_port)
    server.start()