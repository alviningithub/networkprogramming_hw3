import socket
import sys
import json
import threading
import random

class BattleshipServer:
    def __init__(self):
        self.clients = [] # List of sockets
        self.client_data = [] # List of dicts {name, ships_remaining, board_set}
        self.lock = threading.Lock()
        self.turn = 0 # 0 or 1
        self.game_over = False

    def generate_board(self):
        """Generates 3 random ship coordinates on a 5x5 grid."""
        ships = set()
        while len(ships) < 3:
            r, c = random.randint(0, 4), random.randint(0, 4)
            ships.add((r, c))
        return ships

    def broadcast(self, message, exclude_index=None):
        for i, conn in enumerate(self.clients):
            if i != exclude_index:
                try:
                    conn.send((message + "\n").encode())
                except:
                    pass

    def send(self, index, message):
        try:
            self.clients[index].send((message + "\n").encode())
        except:
            pass

    def handle_client_disconnect(self, index):
        if not self.game_over:
            self.game_over = True
            opponent = 1 - index
            self.send(opponent, "GAMEOVER:Opponent disconnected. You Win!")
            print(f"Player {index} disconnected. Game Over.")
            sys.exit(0)

    def game_loop(self):
        # Notify players game is starting
        self.send(0, "INFO:Game Started! You represent Player 1.")
        self.send(1, "INFO:Game Started! You represent Player 2.")
        
        # Player 0 starts
        self.send(0, "TURN:YOUR")
        self.send(1, "TURN:WAIT")

        while not self.game_over:
            try:
                # Receive move from current player
                current_sock = self.clients[self.turn]
                data = current_sock.recv(1024).decode().strip()
                
                if not data: 
                    self.handle_client_disconnect(self.turn)
                    break
                
                # Protocol: "ATTACK:row,col"
                if data.startswith("ATTACK:"):
                    _, coords = data.split(":")
                    r, c = map(int, coords.split(","))
                    
                    opponent = 1 - self.turn
                    opp_ships = self.client_data[opponent]['board_set']
                    
                    if (r, c) in opp_ships:
                        # HIT
                        opp_ships.remove((r, c))
                        self.send(self.turn, f"RESULT:HIT:{r},{c}")
                        self.send(opponent, f"INFO:Opponent HIT your ship at {r},{c}!")
                        
                        # Check Win
                        if len(opp_ships) == 0:
                            self.send(self.turn, "GAMEOVER:WIN")
                            self.send(opponent, "GAMEOVER:LOSE")
                            self.game_over = True
                            # Close connections
                            for sock in self.clients: sock.close()
                            return
                    else:
                        # MISS
                        self.send(self.turn, f"RESULT:MISS:{r},{c}")
                        self.send(opponent, f"INFO:Opponent missed at {r},{c}.")

                    # Switch Turn
                    self.turn = 1 - self.turn
                    self.send(self.turn, "TURN:YOUR")
                    self.send(1 - self.turn, "TURN:WAIT")

                elif data == "EXIT":
                    self.handle_client_disconnect(self.turn)

            except Exception as e:
                # print(f"Error in loop: {e}")
                self.handle_client_disconnect(self.turn)
                break

    def run(self):
        # --- Input: Read JSON from Stdin ---
        try:
            raw_input = sys.stdin.read()
            config = json.loads(raw_input)
            users_count = config.get("users", 2)
        except:
            users_count = 2

        # --- Setup Socket ---
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(('0.0.0.0', 0))
        server_sock.listen(users_count)

        # --- Output: Print Port ---
        print(server_sock.getsockname()[1])
        sys.stdout.flush()

        # --- Wait for Clients ---
        while len(self.clients) < users_count:
            conn, addr = server_sock.accept()
            
            # Protocol: Client sends "ID:Name" immediately on connect
            ident_data = conn.recv(1024).decode().strip()
            name = ident_data.split(":")[1] if ":" in ident_data else "Unknown"
            
            self.clients.append(conn)
            # Generate random board for this player
            self.client_data.append({
                'name': name,
                'board_set': self.generate_board()
            })

        self.game_loop()

if __name__ == "__main__":
    server = BattleshipServer()
    server.run()