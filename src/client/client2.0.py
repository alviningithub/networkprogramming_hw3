import socket
import threading
import json
import os
import shutil
import zipfile
import sys
import re
import subprocess
from time import sleep
from dotenv import load_dotenv

# Import your provided utils including the Exception
from utils.TCPutils import send_json, recv_file, ConnectionClosedByPeer

# Configuration
load_dotenv()
HOST = os.getenv("LOBBY_IP","140.113.17.12")
PORT = int(os.getenv("LOBBY_PORT","20012"))
TEMP_DIR = os.getenv("TEMP_DIR","src/client/client_tmp")  # Where raw downloads land first
DOWNLOAD_BASE_DIR = os.getenv("DOWNLOAD_BASE_DIR","src/client/downloads")

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

        self.start_game_event = threading.Event()
        self.game_set_event = threading.Event()
        self.wait_for_enter = False

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
                # 1. Error Handling Fix: Catch exceptions from recv_file
                metadata, file_path = recv_file(self.sock, TEMP_DIR)
                
                if metadata is None:
                    # None usually implies a timeout if set, or empty read
                    continue

                op = metadata.get("op")
                
                # List of ops that are async notifications
                notifications = [
                    "receive_invite", "invite_accepted", "invite_declined",
                    "receive_request", "request_accepted", "request_declined",
                    "start"
                ]

                if op in notifications:
                    print(f"\n\n*** NOTIFICATION: {metadata.get('message', op)} ***")
                    
                    # Handle specific notification data
                    if op == "receive_invite":
                        print(f"   -> Invite from {metadata.get('fromName')} (Room {metadata.get('roomId')})")
                    elif op == "start":
                        status = metadata.get("status")
                        if status == "error":
                            self.latest_response = metadata
                            self.latest_file_path = file_path
                            self.response_event.set()
                            continue
                        print(f"   -> GAME STARTING on {metadata.get('game_server_ip')}:{metadata.get('game_server_port')}")
                        # Note: In a real implementation, you would launch the game process here.
                        ip = metadata.get("game_server_ip")
                        port = metadata.get("game_server_port")
                        game_name =  metadata.get("game_name")
                        self._start_game(ip,port,game_name)
                    elif op == "request_accepted":
                         # If *I* am the one requesting, and I get accepted, I need to switch to Room Mode
                         rid = metadata.get("roomId")
                         if rid:
                             self.current_room_id = rid
                             print(f"   -> Room ID set to {rid}. Please Go Back to menu to see room options.")

                    # Reprint the prompt so the user knows they can still type
                    print("\n> ", end="", flush=True)

                else:
                    # It's a response to a request the UI thread is waiting for
                    self.latest_response = metadata
                    self.latest_file_path = file_path
                    self.response_event.set()

            except ConnectionClosedByPeer:
                print("\n[Server disconnected - Connection Closed]")
                self.running = False
                os._exit(0)
            except Exception as e:
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

    #start game logic
    def _start_game(self,ip,port,game_name):
        self.game_set_event.clear()
        self.start_game_event.clear()
        self.wait_for_enter = True
        print("\n\n\nPress Enter To Start the Game (Enter)")
        self.response_event.set()
        self.start_game_event.wait()
        # after press enter
        self.wait_for_enter = False
        gamepath = os.path.join(DOWNLOAD_BASE_DIR, str(self.user_id), game_name)
        cwd = os.getcwd()
        os.chdir(gamepath)
        game_client = subprocess.Popen(
            ["uv","run","client/client_main.py",ip,str(port),str(self.user_id),str(self.username)],
            text=True
        )
        os.chdir(cwd)
        game_client.communicate()
        self.game_set_event.set()




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

    def _check_local_version_match(self, game_id, game_name):
        """
        Feature Check:
        Verifies if the local installed version matches the server's latest version.
        Returns True if match.
        Returns False if mismatch, not installed, OR if the game was deleted from the server.
        """
        # 1. Fetch Server Info
        resp, _ = self.send_request({"op": "show_game_data", "game_id": game_id})
        
        if not resp:
             print("[Error] No response from server.")
             return False

        # --- NEW LOGIC START ---
        # If the server returns an error, it means the game ID is invalid 
        # (likely deleted by a developer).
        if resp.get("status") == "error":
            err_msg = resp.get("error", "Unknown error")
            print(f"\n[Error] Game Validation Failed: {err_msg}")
            
            # Specific message if the game is gone
            if "not found" in err_msg.lower():
                print(f"[System] Critical: The game '{game_name}' (ID: {game_id}) has been removed from the server.")
                print("You cannot create rooms, join rooms, or accept invites for this game.")
            return False
        # --- NEW LOGIC END ---
        
        server_version = resp.get("data", {}).get("latest_version")
        
        # 2. Check Local Config
        config_path = os.path.join(DOWNLOAD_BASE_DIR, str(self.user_id), game_name, "config.json")
        local_version = None

        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    data = json.load(f)
                    local_version = data.get("version")
            except Exception as e:
                print(f"[Warning] Failed to read local config: {e}")

        print(f"[Version Check] Game: {game_name} | Local: {local_version} | Server: {server_version}")

        if local_version == server_version:
            return True
        else:
            if local_version is None:
                print(f"[Error] Game '{game_name}' is not installed locally.")
            else:
                print(f"[Error] Version mismatch. You have v{local_version}, but server requires v{server_version}.")
            print("Please go to the Game Store to download/update.")
            return False

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
            if op_code == "register":
                # Feature 1: Don't login after register.
                # Just print success and return to main menu so they can explicitly login.
                # This ensures the server's state (status=online) is handled correctly during login.
                print(f"Registration successful! Please select 'Login' to continue.")
                return 
            
            # Login Success
            self.user_id = resp.get("id")
            self.username = user
            print(f"Success! User ID: {self.user_id}")
            self.menu_stack.append(self.menu_lobby)
        else:
            print(f"Error: {resp.get('error')}")

    # --- 2. Main Lobby Menu ---
# --- 2. Main Lobby Menu ---
    def menu_lobby(self):
        print(f"\n=== Main Lobby ({self.username}) ===")
        print("1. Browse Lobby Status")
        print("2. Enter Game Store")
        print("3. Room & Gameplay")
        print("4. Logout")
        print("5. Rate a Game")  # <--- NEW OPTION
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
            self.current_room_id = None
            self.go_back()
        elif choice == '5':       # <--- NEW HANDLER
            self._handle_add_comment()
        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()
            self.menu_stack.append(self.menu_restart)
            
    def _handle_add_comment(self):
        print("\n--- Rate a Game ---")
        
        # 1. List the games so the user knows the IDs
        print("Fetching game list...")
        games = self._print_games() # Reuse existing helper
        if not games:
            print("No games available to rate.")
            return

        # 2. Get User Input
        game_id = input("Enter Game ID: ").strip()
        comment = input("Enter your comment: ").strip()
        score_str = input("Enter Score (1-5): ").strip()

        # 3. Simple Client-side Validation
        if not game_id or not comment or not score_str:
            print("Error: All fields are required.")
            return
        
        if not score_str.isdigit() or not (1 <= int(score_str) <= 5):
            print("Error: Score must be a number between 1 and 5.")
            return

        # 4. Send Request
        payload = {
            "op": "add_comment",
            "game_id": game_id,
            "content": comment,
            "score": int(score_str)
        }
        
        print("Sending review...")
        resp, _ = self.send_request(payload)

        # 5. Handle Response
        if resp and resp.get("status") == "ok":
            print(f"Success: {resp.get('message')}")
        else:
            print(f"Error: {resp.get('error') if resp else 'No response'}")
        
        input("\nPress Enter to continue...")

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
            
            # --- PRETTY PRINT START ---
            if not users:
                print("   (No users online)")
            else:
                print(f"\n   === {len(users)} User(s) Online ===")
                for u in users:
                    print(f"   > [ID: {u['id']}] {u['name']}")
            print("")
            # --- PRETTY PRINT END ---
            
        elif choice == '2':
            # (Existing room logic...)
            resp, _ = self.send_request({"op": "list_rooms"})
            rooms = resp.get("rooms", [])
            for r in rooms:
                print(f"Room {r['roomId']}: {r['name']} | Game: {r.get('gameName', 'Unknown')} (Host: {r['hostId']}, Status: {r['status']})")
        elif choice == '3':
            self.go_back()
        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()
            self.menu_stack.append(self.menu_restart)

    # --- 2.2 Game Store ---
    # --- 2.2 Game Store ---
    def menu_game_store(self):
        print("\n--- Game Store ---")
        print("1. List Available Games")
        print("2. Inspect Game Info")
        print("3. Download/Update Game")
        print("4. Back")
        print("5. View Game Reviews")  # <--- NEW OPTION
        choice = input("Select: ").strip()

        if choice == '1':
            self._print_games()
        elif choice == '2':
            self._print_games()
            gid = input("Enter Game ID: ")
            resp, _ = self.send_request({"op": "show_game_data", "game_id": gid})
            
            # --- PRETTY PRINT START ---
            if resp.get("status") == "ok":
                data = resp.get("data", {})
                print(f"\n   === Game Details: {data.get('name')} ===")
                print(f"   ID:          {data.get('id')}")
                print(f"   Description: {data.get('description')}")
                print(f"   Version:     v{data.get('latest_version')}")
                print(f"   Owner ID:    {data.get('owner_id')}")
                print("   =================================")
            else:
                print(f"Error: {resp.get('error')}")
        elif choice == '3':
            self._handle_download()
        elif choice == '4':
            self.go_back()
        elif choice == '5':            # <--- NEW HANDLER
            self._handle_view_comments()
        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()
            self.menu_stack.append(self.menu_restart)

    def _handle_view_comments(self):
        print("\n--- View Game Reviews ---")
        
        # 1. List all games first
        print("Fetching game list...")
        games = self._print_games()
        if not games:
            print("No games available.")
            return

        # 2. Pick a Game
        game_id = input("Enter Game ID: ").strip()
        if not any(str(g['game_id']) == game_id for g in games):
            print("Invalid Game ID.")
            return

        # 3. Limit the display number
        limit_input = input("Max comments to display (default 5): ").strip()
        limit = 5
        if limit_input.isdigit():
            limit = int(limit_input)

        # 4. Fetch Data
        print(f"Fetching reviews for Game ID {game_id}...")
        resp, _ = self.send_request({"op": "show_comment", "game_id": game_id})

        if not resp or resp.get("status") != "ok":
            print(f"Error: {resp.get('error', 'Unknown error')}")
            return

        # 5. Display Data
        avg = resp.get("average_score", 0.0)
        comments = resp.get("comments", [])
        
        print(f"\n[Average Score]: {avg} / 5.0 ({len(comments)} total reviews)")
        print(f"--- Recent {min(len(comments), limit)} Comments ---")

        if not comments:
            print("(No comments yet)")
        else:
            # Sort is already DESC from server, just slice
            for i, c in enumerate(comments[:limit]):
                print(f"[{c['timestamp']}] {c['user_name']} (Score: {c['score']})")
                print(f"   > {c['content']}")
                print("-" * 30)

        input("\nPress Enter to continue...")

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

        # Feature 3: download game use id, not name
        target_id = input("Enter Game ID to download: ").strip()
        
        # Resolve ID to Name
        target_name = None
        for g in games:
            if str(g['game_id']) == target_id:
                target_name = g['name']
                break
        
        if not target_name:
            print("Error: Invalid Game ID.")
            return

        print(f"Downloading {target_name}... (Please wait)")
        # Request download using the resolved name
        resp, file_path = self.send_request({"op": "download_game", "game_name": target_name})
        
        if resp.get("status") == "ok" and file_path:
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
            
            # Req 2: List games right before inputting Game ID
            print("Select a game for this room:")
            games = self._print_games()
            g_id = input("Game ID: ")
            
            # Feature 2: Check version before creating
            # Need to find game name to check local files
            selected_game = next((g for g in games if str(g['game_id']) == g_id), None)
            if not selected_game:
                print("Invalid Game ID.")
                return

            if not self._check_local_version_match(g_id, selected_game['name']):
                return # Abort creation
            
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
            # Req 4: Validate ID in available room list
            resp, _ = self.send_request({"op": "list_rooms"})
            rooms = resp.get("rooms", [])
            if resp.get("status") == "ok":
                print("Available Rooms:")
                for r in rooms:
                    print(f"ID: {r['roomId']} | Name: {r['name']} | Game: {r.get('gameName', 'Unknown')}")
            else:
                print("Error fetching rooms.")
                return

            rid = input("Room ID to join: ")
            
            # Validation
            selected_room = next((r for r in rooms if str(r['roomId']) == rid), None)
            if not selected_room:
                print("Error: Room ID not found in the list.")
                return

            # Feature 2: Check version before joining
            game_id = selected_room.get('gameId')
            game_name = selected_room.get('gameName')
            
            if game_id and game_name:
                if not self._check_local_version_match(game_id, game_name):
                    return # Abort joining

            resp, _ = self.send_request({"op": "request", "room_id": rid})
            print(resp.get("message", resp.get("error")))

        elif choice == '3':
            resp, _ = self.send_request({"op": "list_invite"})
            invites = resp.get("invites", [])
            
            # --- PRETTY PRINT START ---
            if not invites:
                print("\n   (No pending invitations)")
            else:
                print(f"\n   === Pending Invitations ({len(invites)}) ===")
                for i in invites:
                    print(f"   > Invite #{i['invite_id']}")
                    print(f"     From: {i['fromName']} (ID: {i['fromId']})")
                    print(f"     Room: '{i['roomName']}' (ID: {i['roomId']})")
                    print(f"     Game: {i.get('gameName', 'Unknown')}")

        elif choice == '4':
            # Req 5: Print invitation details before picking
            resp, _ = self.send_request({"op": "list_invite"})
            invites = resp.get("invites", [])
            
            if not invites:
                print("No pending invitations.")
                return

            print("Pending Invitations:")
            # Format: {"roomId": i[0], "fromId": i[1], "fromName": i[2], "invite_id": i[3]}
            for inv in invites:
                print(f"Invite ID: {inv['invite_id']} | From: {inv['fromName']} (ID: {inv['fromId']}) | Room: {inv['roomId']} | Game: {inv.get('gameName', 'Unknown')}")
            
            i_id = input("Enter Invite ID to reply: ")
            
            # Validate Invite ID exists locally
            selected_invite = next((inv for inv in invites if str(inv['invite_id']) == i_id), None)
            if not selected_invite:
                print("Error: Invalid Invite ID.")
                return

            dec = input("Accept? (y/n): ")
            
            # Feature 1 Check: Verify version on accept
            if dec.lower() == 'y':
                g_id = selected_invite.get('gameId')
                g_name = selected_invite.get('gameName')
                if g_id and g_name:
                    if not self._check_local_version_match(g_id, g_name):
                        print("Aborting invitation acceptance due to game version mismatch.")
                        return

            response = "accept" if dec.lower() == 'y' else "decline"
            resp, _ = self.send_request({"op": "respond_invite", "invite_id": i_id, "response": response})
            print(resp.get("message", resp.get("error")))
            
            # Extract Room ID from message to update status
            if response == "accept" and resp.get("status") == "ok":
                room_id = resp.get("room_id", "")
                self.current_room_id = int(room_id)

        elif choice == '5':
            self.go_back()

        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()

    def _handle_in_room_actions(self, choice):
        if choice == '1':
            resp, _ = self.send_request({"op": "start"})
            if resp is None:
                return
            if resp.get("status") == "error":
                print(f"Cannot start: {resp.get('error')}")
            # Successful start is handled by async notification "start" in listener

        elif choice == '2':
            resp, _ = self.send_request({"op": "leave_room"})
            if resp.get("status") == "ok":
                self.current_room_id = None
                print("Left room.")
        
        elif choice == '3':
            # Req 3: Validate ID in online user list
            resp, _ = self.send_request({"op": "list_online_users"})
            users = resp.get("users", [])

            # --- PRETTY PRINT START ---
            if not users:
                print("\n   (No users online to invite)")
                return
            else:
                print(f"\n   === Online Users ({len(users)}) ===")
                for u in users:
                    # You might want to visually indicate which one is 'me', 
                    # but usually the server includes everyone.
                    marker = " (You)" if str(u['id']) == str(self.user_id) else ""
                    print(f"   > [ID: {u['id']}] {u['name']}{marker}")
            print("   =============================")
            # --- PRETTY PRINT END ---
            
            uid = input("User ID to invite: ")
            
            # Validation
            valid_uids = [str(u['id']) for u in users]
            if uid not in valid_uids:
                print("Error: User ID not found in online list.")
                return

            resp, _ = self.send_request({"op": "invite_user", "invitee_id": uid})
            print(resp.get("message", resp.get("error")))

        elif choice == '4':
            resp, _ = self.send_request({"op": "list_request"})
            requests = resp.get("requests", [])
            
            # --- PRETTY PRINT START ---
            if not requests:
                print("\n   (No pending join requests)")
                return # Exit early if no requests to act on
            else:
                print(f"\n   === Join Requests ({len(requests)}) ===")
                for r in requests:
                    print(f"   > Request #{r['request_id']}")
                    print(f"     From User: {r['fromName']} (ID: {r['fromId']})")
                    print(f"     Target Room: {r['roomId']}")
                    print("     -----------------------------")
            # --- PRETTY PRINT END ---
            
            rid = input("Request ID to reply: ")
            dec = input("Accept? (y/n): ")
            response = "accept" if dec.lower() == 'y' else "decline"
            resp, _ = self.send_request({"op": "respond_request", "request_id": rid, "response": response})
            print(resp.get("message", resp.get("error")))

        elif choice == '5':
            self.go_back()

        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()
            self.menu_stack.append(self.menu_restart)
    
    def menu_restart(self):
        print("\n=== Game Over ===")
        print("1. Restart Game")
        print("2. Back to Room")
        choice = input("Select: ").strip()

        if choice == '1':
            # 1. Send start request exactly like in the room menu
            resp, _ = self.send_request({"op": "start"})
            if resp and resp.get("status") == "error":
                print(f"Error: {resp.get('error')}")
            
            # Note: The listener will catch the "start" notification 
            # and print "Press Enter". The logic below handles that Enter press.

        elif choice == '2':
            # Pop this menu, returning the user to the previous menu (menu_room)
            self.go_back()

        # Handle the synchronization for the Restart case
        elif choice == '' and self.wait_for_enter:
            self.start_game_event.set()
            self.game_set_event.wait()
            # Game finished again. We don't need to append menu_restart
            # because we are already IN menu_restart. The loop will just 
            # show this menu again, creating the cycle you want.

if __name__ == "__main__":
    # Ensure temp dirs exist
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    client = GameClient()
    client.start()