import threading
import queue
import socket
import time
import hashlib
import os
from dotenv import load_dotenv

from utils.TCPutils import *

def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')


class ClientFSM:
    def __init__(self):
        self.state = "init"
        self.event_queue = queue.Queue()
        self.host = None
        self.port = None
        self.sock = None
        self.running = True
        self.user_id = None
        self.u_listener = None
        self.s_listener = None
        self.game_finish = threading.Event()

        # -------------------------
        # State transition table
        # -------------------------
        self.transitions = {
            "init": {
                "connect": "un_auth"
            },
            "un_auth": {
                "login" : "un_auth_wait",
                "register": "un_auth_wait"
            },
            "un_auth_wait": {
                "login_ok": "idle",
                "login_fail": "un_auth",
                "register_ok": "un_auth",
                "register_fail": "un_auth"
            },

            "idle": {
                "request": "idle_wait",
                "create_room" : "idle_wait",
                "logout" : "init",
                "respond_invite" : "idle_wait",
                "list_invite": "idle_wait",
                "list_rooms": "idle_wait",
                "list_online_users": "idle_wait",
                "receive_invite" : "idle",
                "request_accepted": "room",
                "request_declined": "idle"

            },
            "idle_wait":{
                "request_ok": "idle",
                "request_fail": "idle",
                "create_room_ok": "room",
                "create_room_fail" : "idle",
                "respond_invite_ok": "room",
                "respond_invite_fail": "idle",
                "list_invite_ok" : "idle",
                "list_invite_fail" : "idle",
                "list_rooms_ok" : "idle",
                "list_rooms_fail" : "idle",
                "list_online_users_ok" : "idle",
                "list_online_users_fail" : "idle"
            },
            "room":{
                "invite_user" : "room_wait",
                "leave_room":"room_wait",
                "start" : "room_wait",
                "logout" : "init",
                "list_online_users": "room_wait",
                "invite_accepted" : "room",
                "invite_declined" : "room",
                "receive_request" : "room",
                "list_request" : "room_wait",
                "respond_request" : "room_wait",
                "start_ok" : "play",
            },
            "room_wait":{
                "invite_user_ok":"room",
                "invite_user_fail":"room",
                "leave_room_ok":"idle",
                "leave_room_fail":"room",
                "start_ok" : "play",
                "start_fail" : "room",
                "list_online_users_ok" : "room",
                "list_online_users_fail" : "room",
                "list_request_ok" : "room",
                "list_request_fail" : "room",
                "respond_request_ok" : "room",
                "respond_request_fail" : "room"
            },
            "play":{
                "end": "room"
            }
        }

        #-----------------
        #   actions
        #-----------------
        self.actions = {
            ('un_auth', 'login'): self.login,
            ('un_auth', 'register'): self.register,
            ('un_auth_wait', 'login_ok'): self.login_ok,
            ('un_auth_wait', 'login_fail'): self.login_fail,
            ('un_auth_wait', 'register_ok'): self.register_ok,
            ('un_auth_wait', 'register_fail'): self.register_fail,
            ('idle', 'request'): self.request,
            ('idle', 'create_room'): self.create_room,
            ('idle', 'logout'): self.logout,
            ('idle', 'respond_invite'): self.respond_invite,
            ('idle', 'list_invite'): self.list_invite,
            ('idle', 'list_rooms'): self.list_rooms,
            ('idle', 'list_online_users'): self.list_online_users,
            ('idle', 'receive_invite'): self.receive_invite,
            ('idle', 'request_accepted') : self.request_accepted,
            ('idle', 'request_declined') : self.request_declined,
            ('idle_wait', 'request_ok'): self.request_ok,
            ('idle_wait', 'request_fail'): self.request_fail,
            ('idle_wait', 'create_room_ok'): self.create_room_ok,
            ('idle_wait', 'create_room_fail'): self.create_room_fail,
            ('idle_wait', 'respond_invite_ok'): self.respond_invite_ok,
            ('idle_wait', 'respond_invite_fail'): self.respond_invite_fail,
            ('idle_wait', 'list_invite_ok'): self.list_invite_ok,
            ('idle_wait', 'list_invite_fail'): self.list_invite_fail,
            ('idle_wait', 'list_rooms_ok'): self.list_rooms_ok,
            ('idle_wait', 'list_rooms_fail'): self.list_rooms_fail,
            ('idle_wait', 'list_online_users_ok'): self.list_online_users_ok,
            ('idle_wait', 'list_online_users_fail'): self.list_online_users_fail,
            ('room', 'invite_user'): self.invite_user,
            ('room', 'leave_room'): self.leave_room,
            ('room', 'start'): self.start_game,
            ('room', 'logout'): self.logout,
            ('room', 'list_online_users'): self.list_online_users,
            ('room', 'invite_accepted'): self.invite_accepted,
            ('room', 'invite_declined'): self.invite_declined,
            ('room', 'receive_request'): self.receive_request,
            ('room', 'list_request') : self.list_request,
            ('room', 'respond_request') : self.respond_request,
            ('room','start_ok') : self.start_ok,
            ('room_wait', 'invite_user_ok'): self.invite_user_ok,
            ('room_wait', 'invite_user_fail'): self.invite_user_fail,
            ('room_wait', 'leave_room_ok'): self.leave_room_ok,
            ('room_wait', 'leave_room_fail'): self.leave_room_fail,
            ('room_wait', 'start_ok'): self.start_ok,
            ('room_wait', 'start_fail'): self.start_fail,
            ('room_wait', 'list_online_users_ok'): self.list_online_users_ok,
            ('room_wait', 'list_online_users_fail'): self.list_online_users_fail,
            ('room_wait', 'list_request_ok') : self.list_request_ok,
            ('room_wait', 'list_request_fail') : self.list_request_fail,
            ('room_wait', 'respond_request_ok'): self.respond_request_ok,
            ('room_wait', 'respond_request_fail') : self. respond_request_fail,
            ('play', 'end'): self.end,
        }




    # -----------------------------------
    #   Socket connect and start threads
    # -----------------------------------

    def start(self, host="127.0.0.1", port=9000):
        self.sock = create_tcp_socket(host,port)
        self.host = host
        self.port = port
        print("[SYSTEM] Connected to server")

    def close_conn(self):
        self.sock.close()

    # -----------------------------------
    #  FSM Main Loop
    # -----------------------------------
    def fsm_loop(self):
        while self.running:
            event, data = self.event_queue.get()
            if event == "exit":
                break
            clear_terminal()
            print(f"[FSM] Event: {event} (state={self.state})")

            # See if transition is allowed
            next_state = self.transitions.get(self.state, {}).get(event)
            if next_state:
                print(f"--> Transition: {self.state} → {next_state}")
                #callback action
                try:
                    self.actions.get((self.state, event), lambda x: None)(data)
                except Exception as e:
                    print(f"[FSM] Error during action for event '{event}' in state '{self.state}': {e}")
                    self.running = False
                    self.sock.close()
                    break
                self.state = next_state
                self.next_state_action()
            else:
                print(f"[FSM] No transition for event '{event}' in state '{self.state}'")
        
        print("fsm_loop exit")


    # -----------------------------------
    #  Handle server or user actions
    # -----------------------------------
    def next_state_action(self):
        next_state = self.state
        if (next_state == "un_auth"):
            print("Please login or register.")
        elif (next_state == "idle"):
            print("You are now in the lobby. You can request or create a room.")
        elif (next_state == "room"):
            print("You are now in a room. You can invite users or start the game.")
        elif (next_state == "play"):
            print("Game started! Type 'ready' when you are ready.")


    # -----------------------------------
    #  Server listener thread
    # -----------------------------------
    def server_listener(self):
        while self.running:
            try:
                print("[DEBUG] server is listening")
                data = recv_json(self.sock,None)
                if not data:
                    continue

                print(f"[SERVER] {data}")
                

                # convert server message → event
                op = data.get("op")
                status = data.get("status")
                if not op:
                    continue
                if not status:
                    continue

                if op == "invite_accepted":
                    event = "invite_accepted"
                elif op == "receive_invite":
                    event = "receive_invite"
                elif op == "invite_declined":
                    event = "invite_declined" 
                elif op == "receive_request":
                    event = "receive_request" 
                elif op == "request_accepted":
                    event = "request_accepted"
                elif op == "request_declined":
                    event = "request_declined"                    
                else:
                    if status == "ok":
                        event = f"{op}_ok"
                    elif status == "error":
                        event = f"{op}_fail"
                    else:
                        print(data)
                        continue

                self.event_queue.put((event,data))
                #special action after put the event
                #start game , no need server listener now
                if op == "start" and status == "ok":
                    print("server_listener close connection")
                    event_is_set = self.game_finish.wait()
                    self.game_finish.clear()

            except ConnectionClosedByPeer , ConnectionAbortedError:
                print("server_listener_exit")  
                break
      
    # -----------------------------------
    #  User input listener thread
    # -----------------------------------
    

    def user_listener(self):
        """
        Read user input and convert it into FSM events.
        This runs in a separate thread.
        """

        while self.running:
            cmd = input("> ").strip()
            if cmd == "exit":
                print("bye")
                self.event_queue.put(("logout", None))
                self.event_queue.put(("exit",None))
                self.running = False
                time.sleep(1)
                self.sock.close()
                break
            try:
                # ==========================
                #   State: UN_AUTH
                # ==========================
                if self.state == "un_auth":
                    if cmd == "l":
                        print("Logging in...")
                        username = input("Username: ")
                        password = input("Password: ")
                        password_hash = hashlib.sha256(password.encode("utf-8")).digest().hex()
                        data = {"name":username,"passwordHash":password_hash}
                        self.event_queue.put(("login", data))
                    elif cmd == "r":
                        print("Registering...")
                        username = input("Username: ")
                        password = input("Password: ")
                        password_hash = hashlib.sha256(password.encode("utf-8")).digest().hex()
                        data = {"name":username,"passwordHash":password_hash}
                        self.event_queue.put(("register", data))
                    else:
                        raise ValueError("Commands: l = login, r = register")

                # ==========================
                #   State: UN_AUTH_WAIT
                # ==========================
                elif self.state == "un_auth_wait":
                    print("Waiting for server response... no user actions allowed.")

                # ==========================
                #   State: IDLE
                # ==========================
                elif self.state == "idle":
                    if cmd == "req":
                        print("Requesting to join a room...")
                        room_id = int(input("Room ID: "))
                        data = {"room_id":room_id}
                        self.event_queue.put(("request", data))
                    elif cmd == "crt":
                        print("Creating room...")
                        room_name = input("Room name: ")
                        visibility = input("Visibility (public/private): ")
                        while(visibility != "public" and visibility != "private"):
                            print("Visibility need to be public or private")
                            visibility = input("Visibility (public/private): ")
                        
                        
                        data = {"room_name":room_name,"visibility":visibility}
                        self.event_queue.put(("create_room", data))
                    elif cmd == "lo":
                        self.event_queue.put(("logout", None))
                        

                    elif cmd == "resp":
                        invite_id = input("Invite id:")
                        response = input("Your response:(accept|decline)")
                        while(response != "accept" and response != "decline") :
                            print("Response must be accept or decline")
                            response = input("Your response:(accept|decline)")
                        data = {"invite_id":invite_id,"response":response}
                        self.event_queue.put(("respond_invite", data))
                    elif cmd == "linv":
                        self.event_queue.put(("list_invite", None))
                    elif cmd == "lrooms":
                        self.event_queue.put(("list_rooms", None))
                    elif cmd == "lusers":
                        self.event_queue.put(("list_online_users", None))
                    else:
                        raise ValueError(
                            "Commands:\n"
                            "  req        = requset to join room\n"
                            "  crt        = create room\n"
                            "  lo         = logout\n"
                            "  resp       = respond to invite\n"
                            "  linv       = list invites\n"
                            "  lrooms     = list rooms\n"
                            "  lusers     = list users"
                        )

                # ==========================
                #   State: IDLE_WAIT
                # ==========================
                elif self.state == "idle_wait":
                    print("Waiting for server... no user actions allowed.")

                # ==========================
                #   State: ROOM
                # ==========================
                elif self.state == "room":
                    if cmd == "inv":
                        print("Inviting user...")
                        invitee_id = int(input("Enter the user ID to invite: "))
                        data = {"invitee_id": invitee_id}
                        self.event_queue.put(("invite_user", data))
                    elif cmd == "leave":
                        self.event_queue.put(("leave_room", None))
                    elif cmd == "start":
                        self.event_queue.put(("start", None))
                    elif cmd == "lo":
                        self.event_queue.put(("logout", None))
                    elif cmd == "lusers":
                        self.event_queue.put(("list_online_users", None))
                    elif cmd == "lreq":
                        self.event_queue.put(("list_request", None))
                    elif cmd == "resp": 
                        request_id = int(input("Request id:"))
                        response = input("Your response:(accept|decline)")
                        while(response != "accept" and response != "decline") :
                            print("Response must be accept or decline")
                            response = input("Your response:(accept|decline)")
                        data = {"request_id":request_id,"response":response}
                        self.event_queue.put(("respond_request", data))
                    else:
                        raise ValueError(
                            "Commands:\n"
                            "  inv        = invite user\n"
                            "  leave      = leave room\n"
                            "  start      = start game\n"
                            "  lo         = logout\n"
                            "  lusers     = list users\n"
                            "  lreq       = list requests\n"
                            "  resp       = response requests"
                        )

                # ==========================
                #   State: ROOM_WAIT
                # ==========================
                elif self.state == "room_wait":
                    print("Waiting for server... no user actions allowed.")

                # ==========================
                #   State: PLAY
                # ==========================
                elif self.state == "play":
                    # if cmd == "end":
                    #     self.event_queue.put(("end", None))
                    # else:
                        raise ValueError("Command: end = finish game")
                
                #===============================
                # state : init
                #=============================
                elif self.state == "init":
                    continue


                # ==========================
                #   Unknown state (should not happen)
                # ==========================
                else:
                    print(f"[ERROR] Unknown state: {self.state}")

            except ValueError as e:
                print(e)
        print("user_listener_exit")
                

    # -----------------------------------
    #  Action Handlers  
    # -----------------------------------
    def login(self, data):
        send_json(self.sock, {"op": "login", "name": data['name'], "passwordHash": data["passwordHash"]})

    def login_ok(self, data):
        self.user_id = int(data.get("id"))

    def login_fail(self,data):
        print("login fail")
        print(data.get("error"))

    def register(self, data):
        send_json(self.sock, {"op": "register", "name": data['name'], "passwordHash": data["passwordHash"],"role":data["role"]})

    def register_ok(self, data):
        print("Registration successful. You can now log in.")
        print(f"Your user ID is {data.get('id')}")
    
    def register_fail(self,data):
        print("Registration failed.")
        print(data.get("error"))
    
    def logout(self, data):
        print("Logging out...")
        send_json(self.sock, {"op": "logout", "id": self.user_id})
        self.user_id = None
        self.running = False
        time.sleep(1)
        self.sock.close()

    def create_room(self, data):
        send_json(self.sock, {"op": "create_room", "id": self.user_id, "name": data["room_name"] , "visibility": data["visibility"], "gameId": data["gameId"]})

    def create_room_ok(self, data):
        print(f"Room created successfully. Room ID: {data.get('room_id')}")
        self.room_id = int(data.get('room_id'))

    def create_room_fail(self, data):
        print("Failed to create room.")
        print(data.get("error"))

    def list_invite(self, data):
        print("Listing invites...")
        send_json(self.sock, {"op": "list_invite", "id": self.user_id})

    def list_invite_ok(self, data):
        invites = data.get("invites", [])
        if not invites:
            print("No invites.")
        else:
            print("Invites:")
            for invite in invites:
                print(f"Invite ID : {invite['invite_id']} From User ID: {invite['fromId']} Name: {invite['fromName']} to Room ID: {invite['roomId']}")
            
    def list_invite_fail(self, data):
        print("Failed to list invites.")
        print(data.get("error"))

    def list_rooms(self, data):
        print("Listing rooms...")
        send_json(self.sock, {"op": "list_rooms"})

    def list_rooms_ok(self, data):
        rooms = data.get("rooms", [])
        if not rooms:
            print("No available rooms.")
        else:
            print("Available rooms:")
            for room in rooms:
                print(f"Room ID: {room['roomId']} Name: {room['name']} Host ID: {room['hostId']} Status: {room['status']}")
    
    def list_rooms_fail(self, data):
        print("Failed to list rooms.")
        print(data.get("error"))
    
    def list_online_users(self, data):
        print("Listing online users...")
        send_json(self.sock, {"op": "list_online_users", "id": self.user_id})
    
    def list_online_users_ok(self, data):
        users = data.get("users", [])
        if not users:
            print("No online users.")
        else:
            print("Online users:")
            for user in users:
                if int(user['id']) != self.user_id:
                    print(f"User ID: {user['id']} Name: {user['name']}")
    
    def list_online_users_fail(self, data):
        print("Failed to list online users.")
        print(data.get("error"))

    def leave_room(self, data):
        print("Leaving room...")
        send_json(self.sock, {"op": "leave_room"})

    def leave_room_ok(self, data):
        print("Left room successfully.")
    
    def leave_room_fail(self, data):
        print("Failed to leave room.")
        print(data.get("error"))

    def invite_user(self, data):
        send_json(self.sock, {"op": "invite_user", "invitee_id": data["invitee_id"], "room_id": self.room_id})
    
    def invite_user_ok(self, data):
        print("User invited successfully.")

    def invite_user_fail(self, data):
        print("Failed to invite user.")
        print(data.get("error"))
    
    def receive_invite(self, data):
        print(data.get("message"))
    
    def respond_invite(self, data):
        send_json(self.sock, {"op": "respond_invite", "invite_id": data["invite_id"], "response": data["response"]})

    def respond_invite_ok(self, data):
        print("Responded to invite successfully.")
    
    def respond_invite_fail(self, data):
        print("Failed to respond to invite.")
        print(data.get("error"))

    def invite_accepted(self,data):
        print(data.get("message"))

    def invite_declined(self,data):
        print(data.get("message"))

    def request(self,data):
        send_json(self.sock, {"op": "request", "room_id": data["room_id"]})
    
    def request_ok(self,data):
        print("Request has been sent successfully.")
    
    def request_fail(self,data):
        print("Failed to send request.")
        print(data.get("error"))

    def list_request(self,data):
        print("Listing requests...")
        send_json(self.sock, {"op": "list_request"})

    def list_request_ok(self,data):
        requests = data.get("requests", [])
        if not requests:
            print("No pending requests.")
        else:
            print("Pending requests:")
            for req in requests:
                print(f"Request Id: {req['request_id']}  {req['fromName']} wants to join your room, Room ID: {req['roomId']}")
        
    def list_request_fail(self,data):
        print("Failed to list requests.")
        print(data.get("error"))

    def receive_request(self,data):
        print(data.get("message"))
    
    def respond_request(self,data):
        send_json(self.sock, {"op": "respond_request", "request_id": data["request_id"], "response": data["response"]})

    def respond_request_ok(self,data):
        print("Responded to request successfully.")
    
    def respond_request_fail(self,data):
        print("Failed to respond to request.")
        print(data.get("error"))

    def request_accepted(self,data):
        print(data.get("message"))

    def request_declined(self,data):
        print(data.get("message"))

    def start_game(self,data):
        send_json(self.sock,{"op": "start"})

    def start_ok(self,data):
        pass

    def start_fail(self,data):
        print(data.get("message"))

    def end(self,data):
        print(f"You got {data['score']} points!!")
        self.sock = create_tcp_socket(self.host,self.port)
        send_json(self.sock,{"op":"back","userId":self.user_id})
        self.game_finish.set()
        




if __name__ == '__main__':
    client = ClientFSM()
    load_dotenv()
    host = os.getenv("LOBBY_IP")
    # host = input("input the host ip")
    client.start(host,port = 20012)
    # client.register({"name":"b","passwordHash":"b","role":"developer"})
    # data,filename = recv_file(client.sock,"")
    # print(data)
    client.login({"name":"b","passwordHash":"b"})
    data,filename = recv_file(client.sock,"") 
    id = int(data["id"])
    client.user_id = id
    print(data)
    client.create_room({"room_name":"a","visibility":"public","gameId":"1"})
    data,filename = recv_file(client.sock,"") 
    print(data)
    client.leave_room({})
    data,filename = recv_file(client.sock,"") 
    print(data)

    client.logout({""})


