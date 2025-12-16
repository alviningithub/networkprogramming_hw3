import socket
import threading
import json
import os
import shutil
import zipfile
import sys
from time import sleep

# Import your provided utils
from utils.TCPutils import send_json, recv_file

# Configuration
HOST = "140.113.122.54"
PORT = 20012
TEMP_DIR = "src/client/client_tmp"  # Where raw downloads land first
DOWNLOAD_BASE_DIR = "src/client/downloads"

class GameClient:
    def __init__(self):
        self.sock = None
        self.user_id = None
        self.username = None
        self.running = True
        
        # State
        self.current_room_id = None
        self.menu_stack = []  # Stack for menu navigation
        
        # Synchronization
        self.response_event = threading.Event()
        self.latest_response = {}
        self.latest_file_path = None

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((HOST, PORT))
            print(f"Connected to {HOST}:{PORT}")
            
            # Start the listener thread
            t = threading.Thread(target=self._listener_task, daemon=True)
            t.start()
        except Exception as e:
            print(f"Connection failed: {e}")
            sys.exit(1)

    # ---------------------------------------------------------
    # Network Listener (Background Thread)
    # ---------------------------------------------------------
    def _listener_task(self):
        """
        Continuously receives messages.
        - If it's a synchronous response, wake up the main thread.
        - If it's an async notification (invite/request), print it immediately.
        """
        while self.running:
            try:
                # We use recv_file because it handles both pure JSON and File messages automatically
                metadata, file_path = recv_file(self.sock, TEMP_DIR)
                
                if metadata is None:
                    print("\n[Server disconnected]")
                    self.running = False
                    os._exit(0)

                op = metadata.get("op")
                
                # List of ops that are async notifications (not responses to current input)
                notifications = [
                    "receive_invite", "invite_accepted", "invite_declined",
                    "receive_request", "request_accepted", "request_declined",
                    "start"
                ]

                if op in notifications:
                    print(f"\n\n*** NOTIFICATION: {metadata.get('message', op)} ***")
                    if op == "receive_invite":
                        print(f"   -> Invite from {metadata.get('fromName')} (Room {metadata.get('roomId')})")
                    elif op == "start":
                        print(f"   -> GAME STARTING on {metadata.get('game_server_ip')}:{metadata.get('game_server_port')}")
                        # Note: Actual game launch logic would go here
                    
                    # Reprint the prompt so the user knows they can still type
                    print("\n> ", end="", flush=True)

                else:
                    # It's a response to a request the UI thread is waiting for
                    self.latest_response = metadata
                    self.latest_file_path = file_path
                    self.response_event.set()

            except Exception as e:
                # If socket closes or errors
                if self.running:
                    print(f"\n[Connection Error]: {e}")
                    self.running = False
                    os._exit(1)

    # ---------------------------------------------------------
    # Helper: Send & Wait
    # ---------------------------------------------------------
    def send_request(self, payload):
        """Sends JSON and blocks until a response arrives."""
        self.response_event.clear()
        self.latest_response = {}
        self.latest_file_path = None
        
        send_json(self.sock, payload)
        
        # Wait for the listener to set the event
        if not self.response_event.wait(timeout=10.0):
            print("[Error] Server timed out.")
            return None, None
        
        return self.latest_response, self.latest_file_path

    # ---------------------------------------------------------
    # Specific File Handling Logic
    # ---------------------------------------------------------
    def _process_downloaded_game(self, zip_path, game_name):
        """
        Handles the logic: Unzip to downloads/{PlayerId}/{GameName}, 
        cleaning up existing files first.
        """
        if not self.user_id:
            print("[Error] User ID missing for download path.")
            return

        target_dir = os.path.join(DOWNLOAD_BASE_DIR, str(self.user_id), game_name)

        try:
            print(f"Processing game files into: {target_dir}...")

            # 1. Clean existing folder if not empty
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            
            os.makedirs(target_dir, exist_ok=True)

            # 2. Extract the Zip (The server sends a zip file)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(target_dir)

            print(f"Successfully installed {game_name}.")

        except Exception as e:
            print(f"[Error] Failed to install game: {e}")
        finally:
            # 3. Cleanup the temp zip file
            if os.path.exists(zip_path):
                os.remove(zip_path)

    # ---------------------------------------------------------
    # Menus
    # ---------------------------------------------------------
    def start(self):
        self.connect()
        self.menu_stack.append(self.menu_start)
        while self.running and self.menu_stack:
            # Execute the function at the top of the stack
            self.menu_stack[-1]()

    def go_back(self):
        if len(self.menu_stack) > 1:
            self.menu_stack.pop()

    # --- 1. First Menu ---
    def menu_start(self):
        print("\n=== Welcome to Game Client ===")
        print("1. Login")
        print("2. Register")
        print("3. Exit")
        choice = input("Select: ").strip()

        if choice == '1':
            self._handle_auth("login")
        elif choice == '2':
            self._handle_auth("register")
        elif choice == '3':
            self.running = False
            self.sock.close()
            sys.exit(0)

    def _handle_auth(self, op_code):
        user = input("Username: ")
        pwd = input("Password: ")
        resp, _ = self.send_request({"op": op_code, "name": user, "passwordHash": pwd})
        
        if resp and resp.get("status") == "ok":
            self.user_id = resp.get("id")
            self.username = user
            print(f"Success! User ID: {self.user_id}")
            self.menu_stack.append(self.menu_lobby)
        else:
            print(f"Error: {resp.get('error')}")

    # --- 2. Main Lobby Menu ---
    def menu_lobby(self):
        print(f"\n=== Main Lobby ({self.username}) ===")
        print("1. Browse Lobby Status")
        print("2. Enter Game Store")
        print("3. Room & Gameplay")
        print("4. Logout")
        choice = input("Select: ").strip()

        if choice == '1':
            self.menu_stack.append(self.menu_lobby_status)
        elif choice == '2':
            self.menu_stack.append(self.menu_game_store)
        elif choice == '3':
            self.menu_stack.append(self.menu_room)
        elif choice == '4':
            self.send_request({"op": "logout"})
            self.user_id = None
            self.go_back()

    # --- 2.1 Lobby Status ---
    def menu_lobby_status(self):
        print("\n--- Lobby Status ---")
        print("1. List Online Users")
        print("2. List Rooms")
        print("3. Back")
        choice = input("Select: ").strip()

        if choice == '1':
            resp, _ = self.send_request({"op": "list_online_users"})
            users = resp.get("users", [])
            print(f"Online Users: {users}")
        elif choice == '2':
            resp, _ = self.send_request({"op": "list_rooms"})
            rooms = resp.get("rooms", [])
            for r in rooms:
                print(f"Room {r['roomId']}: {r['name']} (Host: {r['hostId']}, Status: {r['status']})")
        elif choice == '3':
            self.go_back()

    # --- 2.2 Game Store ---
    def menu_game_store(self):
        print("\n--- Game Store ---")
        print("1. List Available Games")
        print("2. Inspect Game Info")
        print("3. Download/Update Game")
        print("4. Back")
        choice = input("Select: ").strip()

        if choice == '1':
            self._print_games()
        elif choice == '2':
            gid = input("Enter Game ID: ")
            resp, _ = self.send_request({"op": "show_game_data", "game_id": gid})
            if resp.get("status") == "ok":
                print(json.dumps(resp.get("data"), indent=2))
            else:
                print(f"Error: {resp.get('error')}")
        elif choice == '3':
            self._handle_download()
        elif choice == '4':
            self.go_back()

    def _print_games(self):
        resp, _ = self.send_request({"op": "list_games"})
        games = resp.get("games", [])
        for g in games:
            print(f"ID: {g['game_id']} | Name: {g['name']}")
        return games

    def _handle_download(self):
        print("Available Games:")
        games = self._print_games()
        if not games: return

        # Simple name match for CLI demo
        target_name = input("Enter exact Game Name to download: ").strip()
        
        print("Downloading... (Please wait)")
        # Request download
        # Note: TCPutils.recv_file in the listener will catch the file automatically
        resp, file_path = self.send_request({"op": "download_game", "game_name": target_name})
        
        if resp.get("status") == "ok" and file_path:
            # Here is where we implement your specific folder logic
            self._process_downloaded_game(file_path, target_name)
        else:
            print(f"Download failed: {resp.get('error', 'Unknown error')}")

    # --- 2.3 Room & Gameplay ---
    def menu_room(self):
        # Header showing current status
        status_line = f"Current Room: {self.current_room_id}" if self.current_room_id else "Not in a room"
        print(f"\n--- Room Actions ({status_line}) ---")
        
        if self.current_room_id is None:
            print("1. Create Room")
            print("2. Join Room (Request)")
            print("3. List Invitations")
            print("4. Reply to Invitation")
            print("5. Back")
        else:
            print("1. Launch Game (Start)")
            print("2. Leave Room")
            print("3. Invite User")
            print("4. Reply to Join Request")
            print("5. Back")

        choice = input("Select: ").strip()
        
        if self.current_room_id is None:
            self._handle_no_room_actions(choice)
        else:
            self._handle_in_room_actions(choice)

    def _handle_no_room_actions(self, choice):
        if choice == '1':
            name = input("Room Name: ")
            g_id = input("Game ID: ")
            vis = input("Visibility (public/private): ")
            resp, _ = self.send_request({
                "op": "create_room", "name": name, "gameId": g_id, "visibility": vis
            })
            if resp.get("status") == "ok":
                self.current_room_id = resp.get("room_id")
                print(f"Room {self.current_room_id} created!")
            else:
                print(f"Error: {resp.get('error')}")

        elif choice == '2':
            # List rooms first
            self.send_request({"op": "list_rooms"})
            # Then ask
            rid = input("Room ID to join: ")
            resp, _ = self.send_request({"op": "request", "room_id": rid})
            print(resp.get("message", resp.get("error")))

        elif choice == '3':
            resp, _ = self.send_request({"op": "list_invite"})
            print(resp.get("invites", "No invites"))

        elif choice == '4':
            i_id = input("Invite ID: ")
            dec = input("Accept? (y/n): ")
            response = "accept" if dec.lower() == 'y' else "decline"
            resp, _ = self.send_request({"op": "respond_invite", "invite_id": i_id, "response": response})
            print(resp.get("message", resp.get("error")))
            if response == "accept" and resp.get("status") == "ok":
                # Assuming the message contains room ID or we need to query it. 
                # For simplicity, we just assume user is now in *a* room. 
                # Ideally, the server response would confirm the roomId.
                print("Joined room.")
                # We need to refresh status to find out which room we are in or just assume:
                # In a real app, query "my_status". Here we might just list rooms or wait for a push.
                pass 

        elif choice == '5':
            self.go_back()

    def _handle_in_room_actions(self, choice):
        if choice == '1':
            resp, _ = self.send_request({"op": "start"})
            if resp.get("status") == "error":
                print(f"Cannot start: {resp.get('error')}")
            # Successful start is handled by async notification "start" in listener

        elif choice == '2':
            resp, _ = self.send_request({"op": "leave_room"})
            if resp.get("status") == "ok":
                self.current_room_id = None
                print("Left room.")
        
        elif choice == '3':
            uid = input("User ID to invite: ")
            resp, _ = self.send_request({"op": "invite_user", "invitee_id": uid})
            print(resp.get("message", resp.get("error")))

        elif choice == '4':
            # List requests first
            resp, _ = self.send_request({"op": "list_request"})
            print("Requests:", resp.get("requests"))
            
            rid = input("Request ID to reply: ")
            dec = input("Accept? (y/n): ")
            response = "accept" if dec.lower() == 'y' else "decline"
            resp, _ = self.send_request({"op": "respond_request", "request_id": rid, "response": response})
            print(resp.get("message", resp.get("error")))

        elif choice == '5':
            self.go_back()

if __name__ == "__main__":
    # Ensure temp dirs exist
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    client = GameClient()
    client.start()