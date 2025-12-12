import sqlite3
import socket
import os
from typing import Optional, Any, List, Tuple
from utils.TCPutils import *
from dotenv import load_dotenv
import threading

load_dotenv()
db_host = socket.gethostbyname(socket.gethostname())
db_path = os.getenv("DB_PATH")
db_port = int(os.getenv("DB_PORT"))

##############################################
# Database Service
##############################################

class SQLiteService:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute_sql(self, sql: str, params: Optional[List[Any]] = None) -> Tuple[bool, Any]:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)

            # If it returns rows, fetch them
            rows = cur.fetchall()

            if not sql.strip().lower().startswith("select"):
                conn.commit()
            conn.close()
            return True, rows

        except Exception as e:
            print("error" + str(e))
            return False, str(e)

##############################################
# TCP Server Handling SQL Requests
##############################################

class DBServer:
    def __init__(self, host: str, port: int, db_path: str):
        self.host = host
        self.port = port
        self.db_path = db_path
        self.db = SQLiteService(db_path)
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((host, port))
        self.server_socket.listen(5)
        self.running = False
        self.thread = None

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        print(f"DB server started on {self.host}:{self.port}")
        while(True):
            x = input("input exit to stop the DB server...\n")
            if x == "exit":
                break
            else :
                result = self.db.execute_sql(x)
                print(result)
                
        db_server.stop()

    def _accept_loop(self):
        while self.running:
            try:
                client, addr = self.server_socket.accept()
            except OSError:
                break
            print(f"Client connected: {addr}")
            threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()

    def _handle_client(self, client: socket.socket):
        while True:
            try:
                req = recv_json(client, timeout=30)
            except ConnectionClosedByPeer:
                print("Client disconnected")
                break
            except Exception as e:
                print("[HANDLE CLIENT]" + str(e))
            if req is None:
                continue
            sql = req.get("sql")
            params = req.get("params")
            print(f"Executing SQL: {sql} with params: {params}")
            ok, result = self.db.execute_sql(sql, params)
            if ok:
                send_json(client, {"status": "ok", "data": result})
            else:
                send_json(client, {"status": "error", "error": result})
        client.close()

    def stop(self):
        self.running = False
        self.server_socket.close()
        print("DB server stopped")

# --- Example Usage ---

if __name__ == "__main__":
    # host = input("please input DB machine ip")
    host = "140.113.122.54"
    db_server = DBServer(host, db_port, db_path)
    db_server.start()
    