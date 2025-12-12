from dotenv import load_dotenv
import os
import subprocess
import json
from utils.TCPutils import *
import threading
from typing import Optional
import socket
from typing import Optional, Dict, Any
from DBclient import DatabaseClient
from time import sleep
    
# -------- server part ----------------
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

        # Lock protecting BOTH maps
        self.lock = threading.Lock()

        # Condition for waiting on sending_flag
        self.cond = threading.Condition(self.lock)

        self.db_host = db_host
        self.db_port = db_port

    # -------------------------------------------------------
    # Start server
    # -------------------------------------------------------
    def start(self):
        self.server_socket = create_tcp_passive_socket(self.host, self.port)
        self.is_running = True
        self.server_socket.settimeout(1.0)

        print(f"Server started on {self.host}:{self.port}")

        # Thread that accepts new connections
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()

        # Main thread listens for commands
        self._command_loop()

        # When exit, wait for accept thread to finish
        accept_thread.join()

        print("Server fully stopped.")

    # -------------------------------------------------------
    # Main thread command loop
    # -------------------------------------------------------
    def _command_loop(self):
        while True:
            cmd = input("(input exit to stop the server)").strip()
            if cmd == "exit":
                print("Stopping server...")
                self.is_running = False
                # Closing server_socket will break accept()
                # not true
                self.server_socket.close()
                break

    # -------------------------------------------------------
    # Accept connections in a loop
    # -------------------------------------------------------
    def _accept_loop(self):
        while self.is_running:
            try:
                client_sock, addr = self.server_socket.accept()
            except socket.timeout:
                continue  # check is_running again
            except OSError as e:
                # Happens when server_socket is closed
                print("Server socket closed:", e)
                break

            print(f"Accepted connection from {addr}")

            # IMPORTANT: assign a user_id.
            # In your real system you will receive login message first.
            # For now we use addr as an ID.
            user_port = addr

            # Start a handler thread
            t = threading.Thread(target=self._client_handler, args=(user_port, client_sock), daemon=True)
            t.start()
        print(f"accept loop exit")
        return



    # -------------------------------------------------------
    # Public method: send asynchronously
    # -------------------------------------------------------
    def send_to_client_async(self, user_id, message):
        t = threading.Thread(target=self._send_worker, args=(user_id, message), daemon=True)
        t.start()

    # -------------------------------------------------------
    # Internal: safe send worker
    # -------------------------------------------------------
    def _send_worker(self, user_id, message):
        # Step 1: lock and wait until no one is sending
        with self.cond:
            # If user already disconnected: stop immediately
            if user_id not in self.sending_flag:
                return

            while self.sending_flag[user_id]:
                self.cond.wait()

            # Mark sending
            self.sending_flag[user_id] = True

        # Step 2: get socket
        with self.lock:
            sock = self.client_sockets.get(user_id)

        if sock is None:
            # User disconnected before sending
            with self.cond:
                if user_id in self.sending_flag:
                    self.sending_flag[user_id] = False
                    self.cond.notify_all()
            return

        # Step 3: send
        try:
            send_json(sock, message)
            # print(f"[Send] to {user_id}: {message}")
        except Exception as e:
            pass
            # print(f"[Send Error] to {user_id}: {e}")

        # Step 4: cleanup (but only if user still exists)
        with self.cond:
            if user_id in self.sending_flag:
                self.sending_flag[user_id] = False
                self.cond.notify_all()


    # -------------------------------------------------------
    # Handle one client in a loop
    # -------------------------------------------------------
    def _client_handler(self, user_port, client_sock):
        print(f"[DEBUG] handler started for {user_port}")
        user_id = None
        db = DatabaseClient(self.db_host, self.db_port)
        with client_sock:
            while self.is_running:
                try:
                    msg,filepath = recv_file(client_sock,"temp", timeout=20)
                    if msg is None:
                        continue
                except ConnectionClosedByPeer, ConnectionResetError:
                    break
            

                # print(f"[Recv] from {user_port}: {msg}")
                print(f"[DEBUG] msg from {user_port}: {msg}")


                # -----------------------------------------------------
                # ✅ Extract operation
                # -----------------------------------------------------
                op = msg.get("op")
                if not op and user_id is not None:
                    self.send_to_client_async(user_id, {
                        "status": "error",
                        "op": "unknown",
                        "error": "Missing 'op' field"
                    })
                    continue
                elif not op:
                    send_json(client_sock,{"status":"error", "op": "unknown", "error":"Missing 'op' field"})
                # -----------------------------------------------------
                # ✅ Dispatch operations
                # -----------------------------------------------------
                if op == "print_sockets":
                    self._print_socket_map(db)
                    continue

                if user_id is None:
                    if op == "register":
                        #register user operation
                        self._register_user(msg,client_sock,db)
                        continue
                    elif op == "login":
                        #login user operation
                        user_id = self._login_user(msg,client_sock,db)
                        continue
                    elif op == "back":
                        user_id = self._back_to_lobby(msg,client_sock,db)
                        continue

                if user_id is not  None:
                    if op == "list_rooms":
                        self._list_rooms(user_id,msg,db)
                        continue

                    if op == "logout":
                        self._logout_user(user_id,db)
                        break

                    if op == "list_online_users":
                        self._list_online_users(user_id,db)
                        continue

                    if op == "create_room":
                        self._create_room(msg,user_id,db)
                        continue

                    if op == "leave_room":
                        self._leave_room(user_id,db)
                        continue
                
                    if op == "invite_user":
                        self._invite_user(msg,user_id,db)
                        continue

                    if op == "respond_invite":
                        self._respond_invite(msg,user_id,db)
                        continue

                    if op == "list_invite":
                        self._list_invitation(user_id,db)
                        continue

                    if op == "request":
                        self._request(msg,user_id,db)
                        continue

                    if op == "respond_request":
                        self._respond_request(msg,user_id,db)
                        continue

                    if op == "list_request":
                        self._list_request(user_id,db)
                        continue

                    if op == "start":
                        print(f"[DEBUG] received start op from user {user_id}")
                        success = self._start_game(user_id,db)
                        if success:
                            sleep(0.05)
                            break
                        else :
                            continue

                # Unknown op
                self.send_to_client_async(user_id, {
                    "status": "error",
                    "op": op,
                    "error": f"Unknown op '{op}'"
                })


        # Remove from map when disconnected
        
        db.close()
        if user_id is not None:
            with self.cond:
                # Wait until no one is sending on this socket
                while self.sending_flag.get(user_id, False):
                    self.cond.wait()

                # Now safe to delete
                if user_id in self.client_sockets:
                    del self.client_sockets[user_id]
                if user_id in self.sending_flag:
                    del self.sending_flag[user_id]

                self.cond.notify_all()
        print(f"Client {user_id} disconnected.")

    def _list_rooms(self,user_id,msg,db:DatabaseClient):
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
            self.send_to_client_async(user_id, {
                "status": "ok",
                "op": "list_rooms",
                "rooms": room_list
            })
        except Exception as e:
            print(e)

    def _add_id_socket_mapping(self,user_id,client_sock):
        with self.lock:
            self.client_sockets[user_id] = client_sock
            self.sending_flag[user_id] = False

    def _register_user(self,msg,client_sock,db : DatabaseClient):
        name = msg.get("name")
        passwordHash = msg.get("passwordHash")
        if not name or not passwordHash :
            send_json(client_sock,{"status":"error","op":"register","error":"Missing 'name' or 'passwordHash' field"})
            return None
        try:
            exist = db.find_user_by_name_and_password(name, passwordHash)
        except Exception as e:
            send_json(client_sock,{
                "status": "error",
                "op":"register",
                "error": str(e)
            })
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return None

        if exist:
            send_json(client_sock,{"status":"error","op":"register","error":"User already exists"})
            return None
        try:
            db.insert_user(name, passwordHash,'player')
        except Exception as e:
            send_json(client_sock,{
                "status": "error",
                "op":"register",
                "error": str(e)
            })
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return None
        get_id = db.find_user_by_name_and_password(name, passwordHash)
        send_json(client_sock,{"status":"ok","op":"register","id":get_id[0][0]})
        self._add_id_socket_mapping(get_id[0][0],client_sock)
        return get_id[0][0]
    
    def _login_user(self,msg,client_sock,db : DatabaseClient):
        name = msg.get("name")
        passwordHash = msg.get("passwordHash")
        if not name or not passwordHash:
            send_json(client_sock,{"status":"error","op":"login","error":"Missing 'name' or 'passwordHash' field"})
            return None
        try:
            user = db.find_user_by_name_and_password(name, passwordHash)
            if not user:
                send_json(client_sock,{"status":"error","op":"login","error":"Username Or Password incorrect"})
                return None
            if user[0][4] != 'player':
                send_json(client_sock,{"status":"error","op":"login","error":"Username Or Password incorrect"})
                return None
        except Exception as e:
            send_json(client_sock,{
                "status": "error",
                "op":"login",
                "error": str(e)
            })
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))

            return None
        #change status to online
        try:
            db.update_user(user[0][0], status="online")
        except Exception as e:
            print(f"Failed to update user status: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return None
        
        self._add_id_socket_mapping(user[0][0],client_sock)
        send_json(client_sock,{"status":"ok" , "op": "login","id":user[0][0]})
        return user[0][0]
    
    def _logout_user(self,user_id,db : DatabaseClient):
        #leave room
        try:
            in_room = db.check_user_in_room(user_id)
        except Exception as e:
            print(f"Failed to check user in room: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))    
            return False
        if in_room:
            try:
                room_id = db.leave_room(user_id)
            except Exception as e:
                print(f"Failed to leave room: {e}")
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return False
            #check if room is empty
            try:
                users_in_room = db.list_user_in_room(room_id[0][0])
            except Exception as e:
                print(f"Failed to list users in room: {e}")
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return False
           
            if not users_in_room :
                try:
                    db.delete_room(room_id[0][0])
                except Exception as e:
                    print(f"Failed to delete room: {e}")
                    print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                    return False

        #remove host room 
        try:
            db.delete_room_by_hostid(user_id)
        except Exception as e:
            print(f"Failed to delete room by host id: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
        #remove invitation
        try:
            db.remove_invite_by_toid(user_id)
        except Exception as e:
            print(f"Failed to remove invite by to id: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))

        try:
            db.remove_invite_by_fromid(user_id)
        except Exception as e:
            print(f"Failed to remove invite by from id: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))

        #remove request

        try:
            db.remove_request_by_fromid(user_id)
        except Exception as e:
            print(f"Failed to remove request by user id: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
        
        try:
            db.remove_request_by_toid(user_id)
        except Exception as e:
            print(f"Failed to remove request by to id: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))

        #change status to offline
        try:
            db.update_user(user_id, status="offline")
        except Exception as e:
            print(f"Failed to update user status: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return False
        return True
    
    def _print_socket_map(self,db : DatabaseClient):
        with self.lock:
            print("Current client sockets:")
            for user_id, sock in self.client_sockets.items():
                print(f"User ID: {user_id}, Socket: {sock}")

    def _list_online_users(self,user_id,db : DatabaseClient): 
        try:
            users = db.list_online_users()
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"list_online_users","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        users =  [ {"id":user[0] , "name" : user[1]} for user in users ]
        self.send_to_client_async(user_id,{
            "status": "ok",
            "op": "list_online_users",
            "users": users
        })
    
    def _create_room(self,msg,user_id,db : DatabaseClient): 
        name = msg.get("name")
        visibility = msg.get("visibility")
        status = "idle"
        gameId = msg.get("gameId")
        if not name or not visibility or not status or not gameId:
            self.send_to_client_async(user_id,{"status":"error","op":"create_room","error":"Missing 'name' or 'visibility' or 'gameId' field"})
            return 

            #check user is in a room
        try:
            in_room = db.check_user_in_room(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"create_room","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))    
            return 
        if in_room:
            self.send_to_client_async(user_id,{"status":"error","op":"create_room","error":"User is already in a room"})
            return

        try:
            room_id = db.create_room(name, user_id, visibility, status,gameId)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"create_room","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return 
        self.send_to_client_async(user_id,{
            "status":"ok",
            "op":"create_room",
            "room_id":room_id[0][0]
        })

    def _leave_room(self,user_id,db : DatabaseClient): 
        try:
            in_room = db.check_user_in_room(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"leave_room","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))    
            return 
        if not in_room:
            self.send_to_client_async(user_id,{"status":"error","op":"leave_room","error":"User is not in any room"})
            return
        try:
            room_id = db.leave_room(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"leave_room","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        #check if room is empty
        try:
            users_in_room = db.list_user_in_room(room_id[0][0])
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"leave_room","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        print(users_in_room)
        if not users_in_room :
            try:
                db.delete_room(room_id[0][0])
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"leave_room","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
        self.send_to_client_async(user_id,{
            "status":"ok",
            "op":"leave_room",
            "message":f"Left room {room_id[0][0]}"
        })

    def _invite_user(self,msg,user_id,db : DatabaseClient):
        invitee_id = int(msg.get("invitee_id"))
        if not invitee_id:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":"Missing 'invitee_id' field"})
            return
        #check user exist
        try: 
            invitee = db.find_user_by_id(invitee_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        if not invitee:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":"Invitee user not found"})
            return
        #check inviter is in a room
        try:
            room_id = db.check_user_in_room(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))    
            return
        if not room_id:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":"User is not in any room"})
            return
    
        #add invite to invite list
        try:
            invite_id = db.add_invite(room_id[0][0], invitee_id , user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        #get sender name
        try:
            sender = db.find_user_by_id(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"invite_user","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return

        #async send invite and response
        self.send_to_client_async(user_id,{
            "status":"ok",
            "op":"invite_user",
            "message":f"Invited user {invitee_id} to room {room_id[0][0]}"
        })
        self.send_to_client_async(invitee_id,{
            "status":"ok",
            "op":"receive_invite",
            "message":f"You have been invited to room {room_id[0][0]} by user {sender[0][1]}", 
            "roomId": room_id[0][0], 
            "from_id": user_id, 
            "invite_id": invite_id[0][0],
            "fromName" : sender[0][1]
        })

    def _respond_invite(self,msg,user_id,db : DatabaseClient):
        # get invite response
        response = msg.get("response")  # "accept" or "decline"
        invite_id = int(msg.get("invite_id"))
        if not response or not invite_id:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":"Missing 'response' or 'room_id' field"})
            return
        
        #get invite detail
        try:
            detail = db.get_invite_by_id(invite_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        
        if not detail:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":"Invite not found"})
            return
        
        room_id = detail[0][1]
        inviter_id = detail[0][2]
        invitee_id = detail[0][3]

        if invitee_id != user_id:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":"Invalid invite"})
            print("invalid invite")
            return

        if response == "accept":
            #remove invite from invite list
            try:
                db.remove_invite_by_toid(user_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
            
            try:
                db.remove_invite_by_fromid(user_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
            
            try:
                db.add_user_to_room(room_id, user_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
            self.send_to_client_async(user_id,{
                "status":"ok",
                "op":"respond_invite",
                "message":f"Joined room {room_id}"
            })
            self.send_to_client_async(inviter_id,{
                "status":"ok",
                "op":"invite_accepted",
                "message":f"User {user_id} has accepted your invite to room {room_id}", 
                "roomId": room_id, 
                "from_id": user_id
            })

        elif response == "decline":
            #remove invite from invite list
            try:
                db.remove_invite_by_id(invite_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_invite","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
            
            self.send_to_client_async(user_id,{
                "status":"ok",
                "op":"respond_invite",
                "message":f"Declined invite to room {room_id}"
            })
            self.send_to_client_async(inviter_id,{
                "status":"ok",
                "op":"invite_declined",
                "message":f"User {user_id} has declined your invite to room {room_id}", 
                "roomId": room_id, 
                "from_id": user_id
            })

    def _list_invitation(self,user_id,db : DatabaseClient):
        # get invite from db
        try:
            invites = db.list_invites(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"list_invite","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        # return it 
        invite_list = []
        for invite in invites:
            invite_list.append({
                "roomId": invite[0],
                "fromId": invite[1],
                "fromName": invite[2],
                "invite_id": invite[3]
            })
        self.send_to_client_async(user_id,{
            "status":"ok",
            "op":"list_invite",
            "invites":invite_list
        })

    def _request(self,msg,user_id,db: DatabaseClient): #request to join room by user
        room_id = msg.get("room_id")
        if not room_id:
            self.send_to_client_async(user_id,{"status":"error","op":"request","error":"Missing 'room_id' field"})
            return
        room_id = int(room_id)
        #check room exist 
        try:
            room = db.get_room_by_id(room_id,"public")
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"request","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        
        if not room :
            self.send_to_client_async(user_id,{"status":"error","op":"request","error":"Room not found or not public"})
            return
        #save request to db
        roomhost = int(room[0][2])
        try:
            request_id = db.insert_request(room_id, roomhost ,user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"request","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return  
        if not request_id:
            self.send_to_client_async(user_id,{"status":"error","op":"request","error":"Failed to create request"})
            return
        #send request
        self.send_to_client_async(user_id,{"status":"ok","op":"request", "message": f"Sending join request to room { room_id }"} )
        self.send_to_client_async(roomhost,{"status":"ok","op":"receive_request", "message": f"User { user_id } requests to join room { room_id }" , "room_id": room_id , "from_id": user_id , "request_id" : request_id[0][0]} )

    def _respond_request(self,msg,user_id,db: DatabaseClient):
        request_id = msg.get("request_id")
        response = msg.get("response")  # "accept" or "decline"
        if not request_id or not response:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":"Missing 'request_id' or 'response' field"})
            return
        request_id = int(request_id)
        #get request detail
        try:
            detail = db.get_request_by_id(request_id,user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        
        if not detail:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":"Request not found"})
            return
        
        
        room_id = int(detail[0][1])
        requester_id = int(detail[0][2])
        if response == "accept":
            #remove all request from user
            try:
                db.remove_request_by_userid(requester_id)
            except Exception as e: 
                self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return

            #add user to room
            try:
                db.add_user_to_room(room_id, requester_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
        
        
            
            self.send_to_client_async(user_id,{
                "status":"ok",
                "op":"respond_request",
                "respond":"accept",
                "message":f"User {requester_id} has been added to room {room_id}"
            })
            self.send_to_client_async(requester_id,{
                "status":"ok",
                "op":"request_accepted",
                "respond":"accept",
                "message":f"Your request to join room {room_id} has been accepted", 
                "roomId": room_id
            })

        elif response == "decline":
            #remove request from db
            try:
                db.remove_request_by_id(request_id)
            except Exception as e:
                self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":str(e)})
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                return
            
            self.send_to_client_async(user_id,{
                "status":"ok",
                "op":"respond_request",
                "respond": "declined",
                "message":f"Declined request from user {requester_id} to join room {room_id}"
            })
            self.send_to_client_async(requester_id,{
                "status":"ok",
                "op":"request_declined",
                "respond" : "declined",
                "message":f"Your request to join room {room_id} has been declined", 
                "roomId": room_id
            })
            

    def _list_request(self,user_id,db: DatabaseClient):
        # get request from db
        try:
            requests = db.list_requests(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"respond_request","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        # return it 
        request_list = []
        for request in requests:
            request_list.append({
                "roomId": request[0],
                "fromId": request[1],
                "fromName": request[2],
                "request_id": request[3]
            })
        self.send_to_client_async(user_id,{
            "status":"ok",
            "op":"list_request",
            "requests":request_list
        })

    def _start_game(self,user_id,db: DatabaseClient):
        #check if room is startable
        try:
            roomId = db.check_user_in_room(user_id)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return False
        
        if not roomId:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":"User is not in any room"})
            return False
        roomId = roomId[0][0]
        try:
            room = db.list_user_in_room(roomId)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return False
        if len(room) <2:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":"Not enough players to start the game"})
            return False


        #create a process
        print("[DEBUG] creating process")
        game_server_process = subprocess.Popen(["python", "-u", "tetris_server.py"],stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        
        #get port from process stdout
        game_server_port_line = game_server_process.stdout.readline().decode().strip()
        print(game_server_port_line.split())
        game_server_port = int(game_server_port_line.split()[-1])
        #change rooms status into playing
        try:
            db.update_room(roomId, status="playing")
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return False
        #create gamelog row in db
        try:
            gamelogId = db.create_gamelog(roomId)
        except Exception as e:
            self.send_to_client_async(user_id,{"status":"error","op":"start","error":str(e)})
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return False

        #send game server info to users
        for user in room:
            self.send_to_client_async(user[0],{"status":"ok","op":"start","game_server_ip":self.host,"game_server_port":game_server_port})
        #create a thread to monitor the process
        monitor_thread = threading.Thread(target=self._gameserver_monitor, args=(game_server_process, roomId, gamelogId[0][0]),daemon=True)
        monitor_thread.start()
        
        return True
    
    def _back_to_lobby(self,msg,client_sock,db):
        user_id = int(msg.get("userId"))
        if not user_id :
            return None
        send_json(client_sock,{"op":"back","status":"ok"})
        self._add_id_socket_mapping(user_id,client_sock)
        return user_id
        
    def _gameserver_monitor(self, process, room_id, gamelog_id):
        db = DatabaseClient(self.db_host, self.db_port)

        try:
            # Read all stdout and stderr while waiting
            stdout_data, stderr_data = process.communicate()
        except Exception as e:
            print(f"[Monitor] Error during communicate: {e}")
            return

        # ✅ process.communicate() already calls wait() internally
        # So no need for process.wait()

        # Change room status to idle
        try:
            db.update_room(room_id, status="idle")
        except Exception as e:
            print(f"Failed to update room status: {e}")
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return

        # ✅ Now safely parse the results from stdout
        for line in stdout_data.decode().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line.strip())
                user_id = int(data.get("userId"))
                score = int(data.get("score"))
                db.update_gamelog(gamelog_id, user_id, score)
            except Exception as e:
                print(f"Failed to update gamelog: {e}")
                print("exception occurred at line" + str(e.__traceback__.tb_lineno))
                continue

        # ✅ Ensure all pipes are closed (communicate() usually handles this)
        process.stdout.close()
        process.stderr.close()
        if process.stdin:
            process.stdin.close()

        print(f"[Monitor] Game server for room {room_id} exited cleanly.")

    


# Example usage
if __name__ == "__main__":
    load_dotenv()
    db_port = int(os.getenv("DB_PORT"))
    db_host = os.getenv("DB_IP")
    lobby_host = os.getenv("LOBBY_IP")
    # lobby_host = input("please input lobby machine ip")
    # db_host = input("please input db host ip")
    server = MultiThreadedServer(lobby_host, 20012,db_host,db_port)
    server.start()





