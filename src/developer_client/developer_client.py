import socket
import threading
import os
import shutil
import hashlib
import sys
import time
from dotenv import load_dotenv

# Ensure TCPutils is available
import utils.TCPutils as TCPutils

load_dotenv()

# Configuration
SERVER_IP = os.getenv("SERVER_IP", "127.0.0.1")
SERVER_PORT = int(os.getenv("DEVELOPER_SERVER_PORT", 8000))
GAMES_DIR = "src/developer_client/games"

class CancelAction(Exception):
    """Custom exception to jump back to the main menu."""
    pass

class DeveloperClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.running = False
        
        # State tracking
        self.is_logged_in = False
        self.current_user = None
        
        # Synchronization Primitives for Interactive Mode
        self.expecting_op = None
        self.response_event = threading.Event()
        self.response_data = None

        # Ensure the local games directory exists
        if not os.path.exists(GAMES_DIR):
            os.makedirs(GAMES_DIR)

    def connect(self):
        """Establishes connection and starts the listener thread."""
        try:
            self.sock = TCPutils.create_tcp_socket(self.host, self.port)
            self.running = True
            print(f"[CLIENT] Connected to {self.host}:{self.port}")
            
            self.listen_thread = threading.Thread(target=self.listen_to_server, daemon=True)
            self.listen_thread.start()
            
        except ConnectionRefusedError:
            print("[CLIENT] Error: Could not connect to server. Is it running?")
            sys.exit(1)

    # ==========================================
    #           NETWORK & LISTENER
    # ==========================================

    def listen_to_server(self):
        """Background thread: Listens for responses."""
        while self.running:
            try:
                response = TCPutils.recv_json(self.sock)
                if response is None:
                    print("\n[CLIENT] Server closed connection.")
                    self.running = False
                    self.sock.close()
                    break
                
                # Check if the main thread is waiting for this specific operation
                op = response.get("op")
                if self.expecting_op and op == self.expecting_op:
                    self.response_data = response
                    self.expecting_op = None # Reset expectation
                    self.response_event.set() # Wake up main thread
                else:
                    # If main thread isn't waiting, just print it (Async Notification)
                    self.update_state_from_response(response)
                    self.print_server_response(response)
                    print("\nSelection > ", end="", flush=True)
                
            except TCPutils.ConnectionClosedByPeer:
                print("\n[CLIENT] Disconnected from server.")
                self.running = False
                break
            except Exception as e:
                if self.running:
                    print(f"\n[CLIENT] Listener Error: {e}")
                break

    def wait_for_response(self, op, timeout=5):
        """Blocks main thread until a specific op response arrives."""
        self.response_data = None
        self.expecting_op = op
        self.response_event.clear()
        
        # Send signal to listener to capture the next 'op'
        got_data = self.response_event.wait(timeout)
        
        if not got_data:
            self.expecting_op = None # Timeout cleanup
            return None
        return self.response_data

    def update_state_from_response(self, response):
        """Updates internal flags based on server success/fail messages."""
        op = response.get("op")
        status = response.get("status")
        if status == "OK":
            if op == "login":
                self.is_logged_in = True
            elif op == "logout":
                self.is_logged_in = False
                self.current_user = None

    def print_server_response(self, response):
        """Generic printer for async messages."""
        status = response.get("status", "UNKNOWN")
        message = response.get("message", "")
        op = response.get("op", "notification")
        
        if status == "ERROR":
            print(f"\n[SERVER - {op}] ERROR: {message}")
        else:
            print(f"\n[SERVER - {op}] {message}")

    def send_request(self, op, data=None):
        """Helper to send a JSON request."""
        if not self.running or not self.sock:
            print("[CLIENT] Not connected.")
            return
        payload = {"op": op}
        if data: payload.update(data)
        try:
            TCPutils.send_json(self.sock, payload)
        except Exception as e:
            print(f"[CLIENT] Send Error: {e}")

    # ==========================================
    #           HELPERS & VALIDATION
    # ==========================================

    def get_input(self, prompt_text):
        """Wraps input to handle cancellation."""
        val = input(prompt_text).strip()
        # "Press Esc" isn't standard in Python input(), so we use empty or 'c'
        if val == "" or val.lower() == 'c':
            print("   [Action Cancelled]")
            raise CancelAction()
        return val

    def hash_password(self, password):
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    def validate_and_zip(self, game_folder_name):
        source_path = os.path.join(GAMES_DIR, game_folder_name)
        if not os.path.exists(source_path):
            print(f"   [!] Error: Folder '{source_path}' does not exist.")
            return None
        
        # Check required structure
        required = ["config.json", "client", "server"]
        for item in required:
            if not os.path.exists(os.path.join(source_path, item)):
                print(f"   [!] Error: Missing '{item}' in {source_path}")
                return None
        
        # Zip it
        temp_zip = f"temp_{game_folder_name}" 
        print(f"   [...] Compressing '{game_folder_name}'...")
        zip_path = shutil.make_archive(temp_zip, 'zip', root_dir=GAMES_DIR, base_dir=game_folder_name)
        return zip_path

    # ==========================================
    #           INTERACTIVE ACTIONS
    # ==========================================

    def interactive_pick_game(self, action_verb="select"):
        """Fetches game list and asks user to pick one by number."""
        print(f"   [...] Fetching game list...")
        self.send_request("list_games")
        
        # Wait for listener to get the data
        resp = self.wait_for_response("list_games")
        
        if not resp or resp.get("status") != "OK":
            print(f"   [!] Failed to fetch games: {resp.get('message') if resp else 'Timeout'}")
            return None

        games = resp.get("data", [])
        if not games:
            print("   [!] You have no games uploaded.")
            return None

        print("\n   --- YOUR GAMES ---")
        print(f"   {'No.':<4} | {'Name':<20} | {'Ver':<8}")
        print("   " + "-"*40)
        
        for idx, g in enumerate(games):
            print(f"   {idx+1:<4} | {g['name']:<20} | {g['latestVersion']:<8}")
            
        while True:
            choice = self.get_input(f"\n   Enter number to {action_verb} (or Enter to cancel): ")
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(games):
                    return games[idx] # Return full game dict
                print("   [!] Invalid number.")
            except ValueError:
                print("   [!] Please enter a number.")

    def interactive_pick_version(self, game_name):
        """Fetches version list for a game and asks user to pick one."""
        print(f"   [...] Fetching versions for '{game_name}'...")
        self.send_request("list_versions", {"game_name": game_name})
        
        resp = self.wait_for_response("list_versions")
        
        if not resp or resp.get("status") != "OK":
            print(f"   [!] Failed: {resp.get('message') if resp else 'Timeout'}")
            return None

        versions = resp.get("versions", [])
        if not versions:
            print("   [!] No versions found.")
            return None

        print(f"\n   --- VERSIONS FOR {game_name} ---")
        for idx, v in enumerate(versions):
            print(f"   {idx+1}. {v}")
        
        print(f"   A. ALL VERSIONS (Delete Game)")

        while True:
            choice = self.get_input("\n   Select number or 'A' for All (or Enter to cancel): ").upper()
            if choice == 'A':
                return "ALL"
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(versions):
                    return versions[idx]
                print("   [!] Invalid number.")
            except ValueError:
                print("   [!] Invalid input.")

    # ==========================================
    #           MAIN LOGIC HANDLERS
    # ==========================================

    def run(self):
        print(f"--- Developer Client (Games Dir: ./{GAMES_DIR}/) ---")
        
        while self.running:
            time.sleep(0.2) # UI polish: wait for async prints to settle
            self.print_menu()
            
            try:
                # Direct input (not wrapped in get_input because Enter here creates spacing)
                choice = input("\nSelection > ").strip()
                
                if not self.is_logged_in:
                    self.handle_guest_input(choice)
                else:
                    self.handle_user_input(choice)
            
            except CancelAction:
                # Catch cancellations from sub-menus
                pass 
            except KeyboardInterrupt:
                print("\nExiting...")
                self.running = False
                if self.sock: self.sock.close()
                break
            except Exception as e:
                print(f"[Error] UI Loop: {e}")

    def print_menu(self):
        print("\n" + "="*30)
        if not self.is_logged_in:
            print("      MAIN MENU")
            print("="*30)
            print(" 1. Login")
            print(" 2. Register")
            print(" 0. Exit")
        else:
            print("      DEVELOPER DASHBOARD")
            print("="*30)
            print(" 1. List My Games")
            print(" 2. Upload New Game")
            print(" 3. Update Existing Game")
            print(" 4. Remove Game / Version")
            print(" 5. List Versions of a Game")
            print(" 9. Logout")
            print(" 0. Exit")

    def handle_guest_input(self, choice):
        if choice == '1': # Login
            u = self.get_input("   Username (Enter to cancel): ")
            p = self.get_input("   Password (Enter to cancel): ")
            self.send_request("login", {"username": u, "passwordHash": self.hash_password(p)})
            # We don't block here; listener handles success/fail message

        elif choice == '2': # Register
            u = self.get_input("   New Username: ")
            p = self.get_input("   New Password: ")
            self.send_request("register", {"username": u, "passwordHash": self.hash_password(p)})

        elif choice == '0':
            print("Bye!")
            self.running = False
            if self.sock: self.sock.close()

    def handle_user_input(self, choice):
        # 1. LIST GAMES (Simple async print)
        if choice == '1':
            self.send_request("list_games")

        # 2. UPLOAD NEW (Local folder selection)
        elif choice == '2':
            folder = self.get_input(f"   Enter folder name inside '{GAMES_DIR}/' (or Enter to cancel): ")
            
            zip_path = self.validate_and_zip(folder)
            if zip_path:
                try:
                    print(f"   [...] Uploading {folder}...")
                    TCPutils.send_file(self.sock, zip_path, {"op": "upload_game"})
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)

        # 3. UPDATE EXISTING (Pick from list -> Select Local Folder)
        elif choice == '3':
            # A. Pick Game from Server List
            game = self.interactive_pick_game(action_verb="update")
            if not game: return
            
            game_name = game['name']
            print(f"   Selected: {game_name}")

            # B. Pick Local Folder
            folder = self.get_input(f"   Enter local folder name for update (or Enter to cancel): ")
            
            # C. Verify & Send
            zip_path = self.validate_and_zip(folder)
            if zip_path:
                try:
                    print(f"   [...] Sending update for {game_name}...")
                    TCPutils.send_file(self.sock, zip_path, {"op": "update_game"})
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)

        # 4. REMOVE GAME (Pick Game -> Pick Version)
        elif choice == '4':
            # A. Pick Game
            game = self.interactive_pick_game(action_verb="remove")
            if not game: return
            
            game_name = game['name']
            
            # B. Pick Version
            ver_choice = self.interactive_pick_version(game_name)
            if not ver_choice: return

            payload = {"game_name": game_name}
            
            if ver_choice == "ALL":
                print(f"   [!!!] Deleting ENTIRE game '{game_name}'...")
                payload["version"] = None
            else:
                print(f"   [...] Deleting version '{ver_choice}'...")
                payload["version"] = ver_choice

            self.send_request("remove_game", payload)

        # 5. LIST VERSIONS
        elif choice == '5':
            game = self.interactive_pick_game(action_verb="inspect")
            if game:
                self.send_request("list_versions", {"game_name": game['name']})

        elif choice == '9':
            self.send_request("logout")
        
        elif choice == '0':
            self.running = False
            if self.sock: self.sock.close()
            print("Bye!")

if __name__ == "__main__":
    client = DeveloperClient(SERVER_IP, SERVER_PORT)
    client.connect()
    client.run()