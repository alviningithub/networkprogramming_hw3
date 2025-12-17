import socket
import struct
from utils.TCPutils import create_tcp_socket, send_json, recv_json

"""
database schema
User：{ id, name, passwordHash,status("online"|"offline"), role("player"|"developer") } 
Room：{ id, name, hostUserId, visibility("public"|"private"), status("idle"|"playing"), gameId } 
in_room: {roomId, userId}
request_join_list: {id, roomId, fromId,toId}
game: {id, name, description, OwnerID, LatestVersion}
"""

class DBclientException(Exception):
    def __init__(self, *args):
        super().__init__(*args)

class DatabaseClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.socket = None
        self.connect_db()

    def connect_db(self):
        self.socket = create_tcp_socket(self.host,self.port)

    def _send_request(self, sql: str, params: list = None):
        """Internal method to send a SQL request to the database server."""
        if params is None:
            params = []
        s = self.socket
        send_json(s,{"sql": sql, "params": params})
        response = recv_json(s,1)
        return response
    def close(self):
        self.socket.close()

    def list_all_rooms(self):
        """
        List all rooms.
        Returns: id, name, hostUserId, visibility, status, gameId, gameName
        """
        # Modified to join with Game table to get gameName
        sql = "SELECT R.id, R.name, R.hostUserId, R.visibility, R.status, R.gameId, G.name FROM Room R JOIN Game G ON R.gameId = G.id"
        resp =  self._send_request(sql)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def execute_raw_sql(self, sql: str, params: list = None):
        """Execute raw SQL on the database server."""
        return self._send_request(sql, params)
    
    def find_user_by_name_and_password(self, name: str, passwordHash : str):
        """Find a user by name."""
        sql = "SELECT id, name, passwordHash,status, role FROM User WHERE name = ? AND passwordHash = ? LIMIT 1"
        resp = self._send_request(sql, [name,passwordHash])
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def insert_user(self, name: str, password_hash: str,role: str):
        """Insert a new user into the User table."""
        sql = "INSERT INTO User (name, passwordHash, role) VALUES (?, ?, ?)"
        params = [name, password_hash,role]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp
    
    def update_user(self, user_id: int, name: str = None, password_hash: str = None, status: str = None):
        """Update specified fields of a user by id."""
        fields = []
        params = []

        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if password_hash is not None:
            fields.append("passwordHash = ?")
            params.append(password_hash)
        if status is not None:
            fields.append("status = ?")
            params.append(status)

        if not fields:
            raise ValueError("No fields provided to update")

        sql = f"UPDATE User SET {', '.join(fields)} WHERE id = ?"
        params.append(user_id)

        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp
    
    def list_online_users(self)-> list[list] :
        """List all users with status 'online', excluding developers."""
        sql = "SELECT * FROM User WHERE status = 'online' AND role != 'developer'"
        resp = self._send_request(sql)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp
    
    def create_room (self, name :str , hostUserId : int, visibility : str, status : str, gameId: int) -> int:
        sql = "INSERT INTO Room (name, hostUserId, visibility, status, gameId) VALUES (?, ?, ?, ?,?) RETURNING id"
        params = [name, hostUserId, visibility, status,gameId]
        resp = self._send_request(sql, params)
        
        room_id = -1
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                room_id = resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        
        sql = "INSERT INTO in_room (roomId, userId) VALUES (?, ?) RETURNING *"
        params = [room_id[0][0], hostUserId]
        resp = self._send_request(sql, params)
       
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return room_id
            else:
                raise DBclientException(resp.get("error"))
            
    def check_user_in_room(self, userId: int) -> list[list]:
        sql = "SELECT roomId FROM in_room WHERE userId = ?"
        params = [userId]
        resp = self._send_request(sql, params)
       
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def leave_room(self, userId: int) -> list[list]:
        sql = "DELETE FROM in_room WHERE userId = ? RETURNING roomId"
        params = [userId]
        resp = self._send_request(sql, params)
      
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
        
    def list_user_in_room(self, room_id: int) -> list [list]:
        sql = "SELECT U.id, U.name from in_room as I , User as U where I.userId = U.id AND I.roomId  = ? "
        params = [room_id]
        resp = self._send_request(sql, params)
  
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
       
    def delete_room(self, room_id: int) -> list[list]:
        sql = "DELETE FROM Room WHERE id = ? RETURNING id"
        params = [room_id]
        resp = self._send_request(sql, params)
        
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
    
    def add_invite(self,roomId:int,invitee_id:int , from_id : int) -> list[list]:
        sql = "INSERT INTO invite_list (roomId, fromId, toId) VALUES (?, ?, ?) RETURNING id"
        params = [roomId, from_id, invitee_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))

    def find_user_by_id(self, user_id: int) -> list[list]:
        sql = "SELECT id, name, passwordHash, status, role FROM User WHERE id = ? LIMIT 1"
        params = [user_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
    
    def add_user_to_room(self, roomId:int, userId:int) -> list[list]:
        sql = "INSERT INTO in_room (roomId, userId) VALUES (?, ?) RETURNING roomId"
        params = [roomId, userId]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))

    def remove_invite_by_toid(self,user_id) -> list[list]:
        sql = "DELETE FROM invite_list WHERE toId = ? RETURNING * "
        params = [user_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def remove_invite_by_fromid(self,fromid) -> list[list]:
        sql = "DELETE FROM invite_list WHERE fromId = ? RETURNING * "
        params = [fromid]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
    
    def get_invite_by_id(self,invite_id) -> list[list] :
        sql = "SELECT * FROM invite_list WHERE id = ? "
        params = [invite_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def remove_invite_by_id(self,invite_id) -> list[list]: 
        sql = "DELETE FROM invite_list WHERE id = ? RETURNING * "
        params = [invite_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def get_room_by_id(self, room_id , status = None) -> list[list]:
        sql = "SELECT * FROM Room WHERE id = ? "
        params = [room_id]
        if status is  not None:
            sql += "AND visibility = ?"
            params.append(status)
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))

    def update_room(self,room_id,name = None ,  hostUserId = None, visibility = None, status = None, gameId = None) ->list[list]:
        field = []
        params = []
        if name is not None:
            field.append("name = ?")
            params.append(name)
        if hostUserId is not None:
            field.append("hostUserId = ?")
            params.append(hostUserId)
        if visibility is not None:
            field.append("visibility = ?")
            params.append(visibility)
        if status is not None:
            field.append("status = ?")
            params.append(status)
        if gameId is not None:
            field.append("gameId = ?")
            params.append(gameId)
        if not field:
            raise ValueError("No fields provided to update")
        sql = f"UPDATE Room SET {', '.join(field)} WHERE id = ? RETURNING *"
        params.append(room_id)  
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))

    def list_invites(self,user_id):
        """
        Modified to return Room Name, Game ID, and Game Name.
        """
        sql = """
            SELECT I.roomId, I.fromId, U.name as fromName, I.id, R.name as roomName, R.gameId, G.name as gameName
            FROM invite_list as I
            JOIN User as U ON I.fromId = U.id
            JOIN Room as R ON I.roomId = R.id
            JOIN Game as G ON R.gameId = G.id
            WHERE I.toId = ?
        """
        params = [user_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def insert_request(self,roomId:int,requestee_id:int , from_id : int) -> list[list]:
        sql = "INSERT INTO request_join_list (roomId, fromId, toId) VALUES (?, ?, ?) RETURNING id"
        params = [roomId, from_id, requestee_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def get_request_by_id(self,request_id: int , user_id = None) ->list[list] :
        sql = "SELECT * FROM request_join_list WHERE id = ? "
        params = [request_id]
        if user_id is not None:
            sql += " AND toId = ?"
            params.append(user_id)
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def remove_request_by_userid(self,from_id:int) -> list[list]:
        sql = "DELETE FROM request_join_list WHERE fromId = ? RETURNING * "
        params = [from_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def remove_request_by_id(self,request_id:int) -> list[list]: 
        sql = "DELETE FROM request_join_list WHERE id = ? RETURNING * "
        params = [request_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
    
    def list_requests(self, user_id:int) -> list[list]:
        sql = "SELECT R.roomId , U.id, U.name , R.id FROM request_join_list AS R , User AS U WHERE R.fromId = U.id AND  toId = ?"
        params = [user_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def remove_request_by_fromid(self,fromid):
        sql = "DELETE FROM request_join_list WHERE fromId = ? RETURNING * "
        params = [fromid]
        resp = self._send_request(sql, params)  
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))

    def remove_request_by_toid(self,toid):
        sql = "DELETE FROM request_join_list WHERE toId = ? RETURNING * "
        params = [toid]
        resp = self._send_request(sql, params)  
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
    
    def delete_room_by_hostid(self,hostid):
        pass

    def get_game_by_name(self, game_name: str) -> list[list]:
        sql = "SELECT * FROM Game WHERE name = ? LIMIT 1"
        params = [game_name]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp

    def insert_game(self, name: str, description: str, ownerId: int, latestVersion: str) -> list[list]:
        sql = "INSERT INTO Game (name, description, OwnerID, LatestVersion) VALUES (?, ?, ?, ?) RETURNING id"
        params = [name, description, ownerId, latestVersion]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def insert_game_version(self, game_id: int, version: str, command: str) -> list[list]:
        sql = "INSERT INTO GameVersion (gameId, VersionNumber, command) VALUES (?, ?, ?) RETURNING *"
        params = [game_id, version, command]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    # ... inside DatabaseClient class ...

    def update_game(self, game_id: int, latest_version: str = None, description: str = None) -> list[list]:
        """
        Dynamically updates Game fields (LatestVersion, description).
        """
        fields = []
        params = []

        if latest_version is not None:
            fields.append("LatestVersion = ?")
            params.append(latest_version)
        
        if description is not None:
            fields.append("description = ?")
            params.append(description)

        if not fields:
            raise ValueError("No fields provided to update")

        sql = f"UPDATE Game SET {', '.join(fields)} WHERE id = ? RETURNING *"
        params.append(game_id)

        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
            
    def get_version_by_gameid_and_version(self, game_id: int, version: str) -> list[list]:
        sql = "SELECT * FROM GameVersion WHERE gameId = ? AND VersionNumber = ? LIMIT 1"
        params = [game_id, version]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
    
    def delete_game_by_id(self, game_id: int) -> list[list]:
        sql = "DELETE FROM Game WHERE id = ? RETURNING *"
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
    
    def get_all_games_by_ownerid(self, owner_id: int) -> list[list]:
        sql = "SELECT * FROM Game WHERE OwnerID = ?"
        params = [owner_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else :
                raise DBclientException(resp.get("error"))
        return resp
    
    def delete_game_version_by_id(self, version_id: int) -> list[list]:
        """Deletes a specific version from GameVersion table."""
        sql = "DELETE FROM GameVersion WHERE id = ? RETURNING *"
        params = [version_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def get_ordered_versions_by_gameid(self, game_id: int) -> list[list]:
        sql = "SELECT * FROM GameVersion WHERE gameId = ? ORDER BY UploadDate DESC"
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp
    
    def delete_all_versions_by_gameid(self, game_id: int) -> list[list]:
        """Deletes all versions associated with a specific game ID."""
        sql = "DELETE FROM GameVersion WHERE gameId = ? RETURNING *"
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp
    
    def get_versions_by_game_id(self, game_id):
        sql = "SELECT VersionNumber FROM GameVersion WHERE gameId = ?"
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    # --- Methods added in recent updates ---

    def list_all_games(self) -> list[list]:
        """Returns a list of all games (id, name)."""
        sql = "SELECT id, name FROM Game"
        resp = self._send_request(sql)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def get_game_by_id(self, game_id: int) -> list[list]:
        """Find a game by its ID."""
        sql = "SELECT * FROM Game WHERE id = ? LIMIT 1"
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp

    def get_comments_by_game_id(self, game_id: int) -> list[list]:
        """
        Returns comments for a specific game.
        Joins with User table to get the username.
        """
        sql = """
            SELECT C.id, U.name, C.content, C.score, C.timestamp 
            FROM comment C 
            JOIN User U ON C.userId = U.id 
            WHERE C.gameId = ?
            ORDER BY C.timestamp DESC
        """
        params = [game_id]
        resp = self._send_request(sql, params)
        if isinstance(resp, dict):
            if resp.get("status") == "ok":
                return resp.get("data")
            else:
                raise DBclientException(resp.get("error"))
        return resp