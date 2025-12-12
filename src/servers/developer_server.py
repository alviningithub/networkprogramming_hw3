import socket
import threading
import json
import os
import shutil
import uuid
from dotenv import load_dotenv
from typing import Dict, Any, Tuple
# Ensure TCPutils.py is in the same directory
import utils.TCPutils as TCPutils 

# Database client
from DBclient import DatabaseClient, DBclientException

#db info
load_dotenv()
DB_IP = os.getenv("DB_IP")
DB_PORT = int(os.getenv("DB_PORT")) 
DEVELOPER_SERVER_PORT = int(os.getenv("DEVELOPER_SERVER_PORT"))

# --- Decorator for Dispatching ---
HANDLER_REGISTRY = {}

def request_handler(action_name):
    def decorator(method):
        HANDLER_REGISTRY[action_name] = method
        return method
    return decorator

# --- HELPER FUNCTIONS (Standalone) ---


def extract_zip(zip_filepath: str, temp_root: str) -> Tuple[str, str]:
    """
    Unzips the archive and identifies the content root.
    
    Returns:
        (extract_path, target_root)
        - extract_path: The top-level temp folder (use this for cleanup).
        - target_root: The actual folder containing the game files (use this for logic).
    """
    extract_path = os.path.join(temp_root, str(uuid.uuid4()))
    os.makedirs(extract_path, exist_ok=True)
    
    try:
        shutil.unpack_archive(zip_filepath, extract_path)
    except shutil.ReadError:
        shutil.rmtree(extract_path, ignore_errors=True)
        raise ValueError("File Error: Invalid zip archive.")

    # Detect if the user zipped a folder (nested) or files (flat)
    target_root = extract_path
    
    # If config.json isn't at the top, check if there is exactly one folder
    if not os.path.exists(os.path.join(extract_path, "config.json")):
        items = os.listdir(extract_path)
        # Filter out system files like __MACOSX just in case
        valid_items = [i for i in items if not i.startswith("__")]
        
        if len(valid_items) == 1:
            potential_root = os.path.join(extract_path, valid_items[0])
            if os.path.isdir(potential_root):
                target_root = potential_root

    return extract_path, target_root

def get_config(root_path: str) -> Dict[str, Any]:
    """
    Reads and validates the config.json file from the given root.
    """
    config_path = os.path.join(root_path, "config.json")
    
    if not os.path.exists(config_path):
        raise ValueError("Config Error: 'config.json' missing from root.")
        
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except json.JSONDecodeError:
        raise ValueError("Config Error: 'config.json' is not valid JSON.")

    required_fields = ["name", "version", "description", "command"]
    missing = [k for k in required_fields if k not in config]
    empty = [k for k in required_fields if config[k] == '']
    
    if missing:
        raise ValueError(f"Config Error: Missing required fields: {",".join(missing)}")
    if empty:
        raise ValueError(f"Config Error: Critical config message not filled", {",".join(empty)})

    return config

def check_folder_structure(root_path: str):
    """
    Validates that the folder contains the required file hierarchy:
    - client/client_main.py
    - server/server_main.py
    """
    client_dir = os.path.join(root_path, "client")
    client_main = os.path.join(client_dir, "client_main.py")
    server_dir = os.path.join(root_path, "server")
    server_main = os.path.join(server_dir, "server_main.py")

    if not os.path.isdir(client_dir):
        raise ValueError("Structure Error: 'client' directory missing.")
        
    if not os.path.isfile(client_main):
        raise ValueError("Structure Error: 'client/client_main.py' missing.")

    if not os.path.isdir(server_dir):
        raise ValueError("Structure Error: 'server' directory missing.")

    if not os.path.isfile(server_main):
        raise ValueError("Structure Error: 'server/server_main.py' missing.")

class GameShopServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False  # Flag to control the server loop
        
        # Storage Configuration
        self.storage_dir = "src/servers/uploaded_games"
        self.temp_dir = "src/servers/server_temp"
        os.makedirs(self.storage_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)



    def start(self):
        """Initializes the socket, starts the input monitor, and enters the accept loop."""
        self.server_socket = TCPutils.create_tcp_passive_socket(self.host, self.port)
        self.running = True
        
        # Start the Input Monitor Thread
        # We make it a daemon thread so it doesn't block program exit if something else fails
        input_thread = threading.Thread(target=self.monitor_input, daemon=True)
        input_thread.start()

        print(f"[SERVER] Listening on {self.host}:{self.port}")
        print("[SERVER] Type 'exit' and press Enter to stop the server.")

        try:
            while self.running:
                try:
                    # accept() blocks here. 
                    # If server_socket.close() is called by the input thread, this raises OSError.
                    conn, addr = self.server_socket.accept()
                    
                    # Spawn a worker thread for the new client
                    t = threading.Thread(target=self.handle_client, args=(conn, addr))
                    t.daemon = True 
                    t.start()
                    
                except OSError:
                    # This block runs when the socket is closed while waiting
                    if not self.running:
                        # This is the expected behavior during shutdown
                        break 
                    else:
                        # A real error occurred (not a shutdown)
                        raise 

        except Exception as e:
            print(f"[SERVER] Critical Error: {e}")
        finally:
            self.shutdown_cleanup()

    def monitor_input(self):
        """Runs in a separate thread to listen for admin commands."""
        while self.running:
            try:
                # Blocks until user types something
                cmd = input() 
                if cmd.strip().lower() == "exit":
                    print("[SERVER] Shutdown sequence initiated...")
                    self.running = False
                    
                    # CRITICAL: Close the socket to break the main thread's accept() block
                    if self.server_socket:
                        self.server_socket.close()
                    break
            except EOFError:
                # Handle cases where input stream is closed (e.g., background process)
                break

    def shutdown_cleanup(self):
        """Clean up resources before exiting."""
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        
        # Optional: Clean up temp directory
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
            os.makedirs(self.temp_dir)
            
        print("[SERVER] Server Stopped Gracefully.")

    def handle_client(self, conn: socket.socket, addr):
        """The main loop for a single client connection."""
        print(f"[NEW CONN] {addr} connected.")
        session = {"userId": None, "user": None}
        db = DatabaseClient(DB_IP,DB_PORT)
        try:
            while self.running: # Check running flag here too
                metadata, temp_filepath = TCPutils.recv_file(conn, self.temp_dir)

                if metadata is None:
                    break 

                action = metadata.get("op")
                handler = HANDLER_REGISTRY.get(action)
                
                if handler:
                    handler(self, conn, metadata, temp_filepath, session,db)
                else:
                    print(f"[WARN] Unknown action: {action}")
                    TCPutils.send_json(conn, {"status": "ERROR", "message": "Unknown command"})
                    if temp_filepath and os.path.exists(temp_filepath):
                        os.remove(temp_filepath)

        except TCPutils.ConnectionClosedByPeer:
            print(f"[DISCONNECT] {addr} closed connection.")
        except Exception as e:
            print(f"[ERROR] {addr}: {str(e.__traceback__.tb_lineno)}")
        finally:
            conn.close()

    # ==========================================
    #            COMMAND HANDLERS
    # ==========================================

    @request_handler("register")
    def handle_register(self, conn, request, filepath, session,db:DatabaseClient):
        username = request.get('username')
        passwordHash = request.get('passwordHash')

        if not username or not passwordHash:
            TCPutils.send_json(conn,{"status": "ERROR","op":"register" , "message": "missing username or passwordHash "})

        try:        
            user = db.find_user_by_name_and_password(username,passwordHash)
            if user: 
                TCPutils.send_json(conn, {"status": "ERROR","op":"register", "message": "User exists"})
                return 
            db.insert_user(username,passwordHash,'developer')
            os.makedirs(os.path.join(self.storage_dir, username), exist_ok=True)
            TCPutils.send_json(conn, {"status": "OK", "op":"register","message": "Registered successfully"})
        except DBclientException  as e:
            TCPutils.send_json(conn,{
                "status": "error",
                "op":"register",
                "error": str(e)
            })
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))


    @request_handler("login")
    def handle_login(self, conn, request, filepath, session,db:DatabaseClient):
        username = request.get('username')
        passwordHash = request.get('passwordHash')

        try:
            user = db.find_user_by_name_and_password(username,passwordHash)
            if not user:
                TCPutils.send_json(conn, {"status": "ERROR", "op":"login", "message": "Invalid credentials"})
                return
            if user[0][4] != 'developer':
                TCPutils.send_json(conn, {"status": "ERROR", "op":"login", "message": "Not a developer account"})
                return
            session["user"] = username
            session["userId"] = user[0][0]
            db.update_user(session["userId"], status='online')
            TCPutils.send_json(conn, {"status": "OK", "op":"login", "message": f"Welcome {username}"})
            return

        except DBclientException as e:
            TCPutils.send_json(conn,{
                "status": "error",
                "op":"login",
                "error": str(e)
            })
            print("exception occurred at line" + str(e.__traceback__.tb_lineno))
            return
        
    @request_handler("logout")
    def handle_logout(self, conn, request, filepath, session,db):
        session["user"] = None
        session["userId"] = None
        TCPutils.send_json(conn, {"status": "OK", "op":"logout" ,"message": "Logged out"})

    # --- HANDLER: Upload New Game ---
    @request_handler("upload_game")
    def handle_upload(self, conn, request, filepath, session, db:DatabaseClient):
        user = session["user"]
        userId = session["userId"]
        if not user:
            TCPutils.send_json(conn, {"status": "ERROR","op":"upload_game", "message": "Auth required"})
            if filepath: os.remove(filepath)
            return
        
        if not filepath:
            TCPutils.send_json(conn, {"status": "ERROR","op":"upload_game", "message": "No file"})
            return

        extract_path = None
        try:
            # 1. Extract
            extract_path, target_root = extract_zip(filepath, self.temp_dir)
            
            # 2. Check Structure
            check_folder_structure(target_root)
            
            # 3. Get Config
            config = get_config(target_root)
            game_name = config["name"]
            version = config["version"]
            command = config["command"]

            # 4. Logic & Move
            # check if the same name exist
            result = db.get_game_by_name(game_name)
            if result:
                raise Exception("Game exists.")
            #insert into database
            #insert game
            game_id = db.insert_game(game_name, config["description"], userId, version)
            #insert version
            version_id = db.insert_game_version(game_id[0][0], version, command)
            #clear garbage if any
            dest_path = os.path.join(self.storage_dir, str(userId), game_name, version)
            if os.path.exists(dest_path): shutil.rmtree(dest_path)
            shutil.move(target_root, dest_path)
            TCPutils.send_json(conn, {"status": "OK", "op":"handle_upload" ,  "message": f"Uploaded {game_name} v{version}"})
        except DBclientException as e:
            TCPutils.send_json(conn, {"status": "ERROR", "op":"handle_upload" , "message": str(e)})
        except Exception as e:
            TCPutils.send_json(conn, {"status": "ERROR", "op":"handle_upload" ,  "message": str(e)})
        finally:
            # Cleanup: remove the received zip
            if filepath and os.path.exists(filepath): os.remove(filepath)
            # Cleanup: remove the temporary extraction folder (if it still exists)
            # Note: shutil.move might have moved the subfolder, but the UUID parent folder (extract_path) remains
            if extract_path and os.path.exists(extract_path): shutil.rmtree(extract_path, ignore_errors=True)

    @request_handler("update_game")
    def handle_update(self, conn, request, filepath, session,db:DatabaseClient):
        user = session["user"]
        userId = session["userId"]
        if not user:
            TCPutils.send_json(conn, {"status": "ERROR","op":"update_game", "message": "Auth required"})
            if filepath: os.remove(filepath)
            return

        if not filepath:
            TCPutils.send_json(conn, {"status": "ERROR","op":"update_game","message": "No file"})
            return

        extract_path = None
        try:
            # 1. Extract
            extract_path, target_root = extract_zip(filepath, self.temp_dir)
            
            # 2. Check Structure
            check_folder_structure(target_root)
            
            # 3. Get Config
            config = get_config(target_root)
            game_name = config["name"]
            version = config["version"]

            # 4. Logic & Move
            # check if the game exist
            game = db.get_game_by_name(game_name)
            if not game:
                raise Exception("Game not found.")
            gameid = game[0][0]
            # check if version exists
            version_record = db.get_version_by_gameid_and_version(gameid, version)
            if version_record:
                raise Exception("Version exists.")
            # check if the user is the owner
            if game[0][3] != userId:
                raise Exception("Not the owner.")
            # update latest version
            db.update_game_version(gameid, version)

            #update database
            version_id = db.insert_game_version(gameid, version, config["command"])
            #move files
            dest_path = os.path.join(self.storage_dir, str(userId), game_name, version)
            if os.path.exists(dest_path): shutil.rmtree(dest_path)
            shutil.move(target_root, dest_path)

            TCPutils.send_json(conn, {"status": "OK", "message": f"Updated {game_name} v{version}"})

        except Exception as e:
            TCPutils.send_json(conn, {"status": "ERROR", "message": str(e)})
        finally:
            if filepath and os.path.exists(filepath): os.remove(filepath)
            if extract_path and os.path.exists(extract_path): shutil.rmtree(extract_path, ignore_errors=True)

    @request_handler("remove_game")
    def handle_remove(self, conn, request, filepath, session, db: DatabaseClient):
        user = session["user"]
        userid = session["userId"]
        if not user:
            TCPutils.send_json(conn, {"status": "ERROR", "op": "remove_game", "message": "Auth required"})
            return

        game_name = request.get("game_name")
        version_to_remove = request.get("version") # May be None or empty string

        if not game_name:
            TCPutils.send_json(conn, {"status": "ERROR", "op": "remove_game", "message": "Missing game_name"})
            return

        try:
            # 1. Find Game and Verify Owner (Common Step)
            game = db.get_game_by_name(game_name)
            if not game:
                raise Exception("Game not found.")
            
            game_id = game[0][0]
            owner_id = game[0][3]
            current_latest_version = game[0][4]

            if owner_id != userid:
                raise Exception("Not the owner.")

            # ==========================================
            # BRANCH A: Remove Specific Version
            # ==========================================
            if version_to_remove: 
                # 1. Find specific version ID
                target_version_record = db.get_version_by_gameid_and_version(game_id, version_to_remove)
                if not target_version_record:
                    raise Exception("Version not found.")
                target_version_id = target_version_record[0][0]

                # 2. Delete version from DB
                db.delete_game_version_by_id(target_version_id)
                
                # 3. Delete physical files for this version
                version_path = os.path.join(self.storage_dir, str(userid), game_name, version_to_remove)
                if os.path.exists(version_path):
                    shutil.rmtree(version_path)

                # 4. Handle Promotion / Cleanup
                remaining_versions = db.get_ordered_versions_by_gameid(game_id)
                
                msg = ""
                if not remaining_versions:
                    # No versions left -> Delete the Game entirely
                    db.delete_game_by_id(game_id)
                    game_root_path = os.path.join(self.storage_dir, str(userid), game_name)
                    if os.path.exists(game_root_path):
                        shutil.rmtree(game_root_path)
                    msg = f"Removed version {version_to_remove}. No versions left, game deleted."
                
                else:
                    # Versions remain. Promote if we deleted the latest one.
                    if version_to_remove == current_latest_version:
                        # remaining_versions is sorted DESC, so index 0 is the new latest
                        new_latest = remaining_versions[0][2]
                        db.update_game_version(game_id, new_latest)
                        msg = f"Removed {version_to_remove}. Promoted {new_latest} to latest."
                    else:
                        msg = f"Removed version {version_to_remove}."

                TCPutils.send_json(conn, {"status": "OK", "op": "remove_game", "message": msg})

            # ==========================================
            # BRANCH B: Remove Whole Game (No version specified)
            # ==========================================
            else:
                # 1. Delete all versions from DB first (to avoid orphans)
                db.delete_all_versions_by_gameid(game_id)
                
                # 2. Delete the Game record
                db.delete_game_by_id(game_id)
                
                # 3. Remove the entire game directory (contains all versions)
                game_root_path = os.path.join(self.storage_dir, str(userid), game_name)
                if os.path.exists(game_root_path):
                    shutil.rmtree(game_root_path)
                
                TCPutils.send_json(conn, {"status": "OK", "op": "remove_game", "message": f"Game '{game_name}' and all versions deleted."})

        except Exception as e:
            print(f"Error in remove_game: {e}")
            TCPutils.send_json(conn, {"status": "ERROR", "op": "remove_game", "message": str(e)})

    @request_handler("list_games")
    def handle_list(self, conn, request, filepath, session,db:DatabaseClient):
        user = session["user"]
        userId = session["userId"]
        if not user:
            TCPutils.send_json(conn, {"status": "ERROR","op":"list_games" ,"message": "Auth required"})
            return
        try:
            data = db.get_all_games_by_ownerid(userId)  
            # format (id, name, description, OwnerId, LatestVersion)
            data_dict = []
            for game in data:
                data_dict.append({
                    "id": game[0],
                    "name": game[1],
                    "description": game[2],
                    "ownerId": game[3],
                    "latestVersion": game[4]
                })
            TCPutils.send_json(conn, {"status": "OK","op":"list_games" , "data": data_dict})
        except DBclientException as e:
            TCPutils.send_json(conn, {"status": "ERROR", "op":"list_games" ,"message": str(e)})
        except Exception as e:
            TCPutils.send_json(conn, {"status": "ERROR","op":"list_games" , "message": str(e)})


    @request_handler("list_versions")
    def handle_list_versions(self, conn, request, filepath, session, db: DatabaseClient):
        user = session['user']
        userId = session['userId']

        # 1. Authentication Check
        if not session.get("user"):
            TCPutils.send_json(conn, {"status": "ERROR", "op": "list_versions", "message": "Auth required"})
            return

        # 2. specific parameters
        game_name = request.get("game_name")
        if not game_name:
            TCPutils.send_json(conn, {"status": "ERROR", "op": "list_versions", "message": "Missing game_name"})
            return

        try:
            # 3. Find the Game ID
            game = db.get_game_by_name(game_name)
            if not game:
                TCPutils.send_json(conn, {"status": "ERROR", "op": "list_versions", "message": "Game not found"})
                return
            
            # Assuming game[0][0] is the ID based on existing code
            game_id = game[0][0]

            # 4. Fetch Versions (Requires DB Update, see below)
            # expected format: [ ("1.0",), ("1.1",) ]
            version_records = db.get_versions_by_game_id(game_id)
            
            # Flatten the list of tuples into a simple list of strings
            version_list = [v[0] for v in version_records]

            TCPutils.send_json(conn, {
                "status": "OK", 
                "op": "list_versions", 
                "versions": version_list,
                "message": f"Found {len(version_list)} versions"
            })

        except Exception as e:
            TCPutils.send_json(conn, {"status": "ERROR", "op": "list_versions", "message": str(e)})

if __name__ == "__main__":
    server = GameShopServer("0.0.0.0", DEVELOPER_SERVER_PORT)
    server.start()