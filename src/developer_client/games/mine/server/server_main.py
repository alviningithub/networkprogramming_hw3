import socket
import threading
import json
import sys
import random
import time

# --- Configuration ---
GRID_SIZE = 8
NUM_MINES = 10

class MinesweeperServer:
    def __init__(self):
        self.clients = []
        self.player_data = {} 
        self.lock = threading.Lock()
        
        self.board = [] 
        self.revealed = set() 
        self.flags = {} 
        self.turn_index = 0
        self.game_over = False
        self.total_safe_cells = (GRID_SIZE * GRID_SIZE) - NUM_MINES
        self.revealed_safe_count = 0

    def generate_board(self):
        self.board = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        mines_placed = 0
        while mines_placed < NUM_MINES:
            r, c = random.randint(0, GRID_SIZE-1), random.randint(0, GRID_SIZE-1)
            if self.board[r][c] != -1:
                self.board[r][c] = -1
                mines_placed += 1
        
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                if self.board[r][c] == -1: continue
                count = 0
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE and self.board[nr][nc] == -1:
                            count += 1
                self.board[r][c] = count

    def broadcast(self, message):
        data = json.dumps(message) + "\n"
        dead_clients = []
        for client in self.clients:
            try:
                client.sendall(data.encode('utf-8'))
            except:
                dead_clients.append(client)
        for dc in dead_clients:
            self.remove_client(dc)

    def remove_client(self, sock):
        if sock in self.clients:
            self.clients.remove(sock)
            if sock in self.player_data:
                del self.player_data[sock]

    def handle_client(self, client_sock):
        buffer = ""
        while True:
            try:
                data = client_sock.recv(1024).decode('utf-8')
                if not data: break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line: continue
                    self.process_request(client_sock, json.loads(line))
            except:
                break
        self.remove_client(client_sock)
        client_sock.close()

    # --- NEW: Flood Fill Logic ---
    def perform_flood_fill(self, start_r, start_c, pid):
        """Recursively reveals empty connected cells and scores 1 pt per 3 blocks."""
        updates = []
        queue = [(start_r, start_c)]
        cells_revealed_in_this_move = 0 # Counter for the nerf logic
        
        while queue:
            r, c = queue.pop(0)
            
            if (r, c) in self.revealed:
                continue

            self.revealed.add((r, c))
            val = self.board[r][c]
            self.revealed_safe_count += 1
            cells_revealed_in_this_move += 1 # Count cell
            
            # Note: We do NOT add score here anymore
            
            updates.append({'r': r, 'c': c, 'val': val, 'player': pid})

            # If it's a "0" (empty), add neighbors to queue
            if val == 0:
                for dr in [-1, 0, 1]:
                    for dc in [-1, 0, 1]:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE:
                            if (nr, nc) not in self.revealed:
                                queue.append((nr, nc))
        
        # --- NEW SCORING LOGIC ---
        # 1 point for every 3 blocks revealed (integer division)
        points_earned = cells_revealed_in_this_move // 3
        
        if points_earned > 0:
            for s in self.player_data:
                if self.player_data[s]['id'] == pid:
                    self.player_data[s]['score'] += points_earned
                    
        return updates

    def process_request(self, sock, req):
        if req['type'] == 'CONNECT':
            self.player_data[sock]['name'] = req['name']
            self.player_data[sock]['id'] = req['id']
            if len(self.clients) == self.expected_users:
                 self.start_game()

        elif req['type'] == 'MOVE':
            with self.lock:
                if self.game_over: return

                current_sock = self.clients[self.turn_index]
                if sock != current_sock: return 

                r, c = req['r'], req['c']
                action = req['action']
                pid = self.player_data[sock]['id']

                # --- BUG FIX: Prevent re-clicking revealed cells ---
                if (r, c) in self.revealed:
                    return

                updates_to_broadcast = []

                if action == 'REVEAL':
                    val = self.board[r][c]
                    
                    if val == -1: # Mine
                        self.revealed.add((r, c))
                        self.player_data[sock]['score'] -= 3
                        updates_to_broadcast.append({'r': r, 'c': c, 'val': 'MINE', 'player': pid})
                    
                    elif val == 0: # Empty -> Trigger Spread
                        updates_to_broadcast = self.perform_flood_fill(r, c, pid)
                    
                    else: # Number -> Single Reveal
                        self.revealed.add((r, c))
                        self.player_data[sock]['score'] += 1
                        self.revealed_safe_count += 1
                        updates_to_broadcast.append({'r': r, 'c': c, 'val': val, 'player': pid})

                elif action == 'TAG':
                    # Toggle flag or set flag. In this simple version, we just set it.
                    # We do allow moving flags, but not on revealed cells.
                    self.flags[(r,c)] = pid
                    updates_to_broadcast.append({'r': r, 'c': c, 'val': 'FLAG', 'player': pid})

                # Broadcast all updates (Flood fill might generate many)
                for update in updates_to_broadcast:
                    update['type'] = 'UPDATE'
                    self.broadcast(update)

                # Check End Game
                if self.revealed_safe_count >= self.total_safe_cells:
                    self.end_game()
                else:
                    self.turn_index = (self.turn_index + 1) % len(self.clients)
                    self.broadcast_scores()
                    self.broadcast_turn()

    def start_game(self):
        self.generate_board()
        names = [self.player_data[c]['name'] for c in self.clients]
        self.broadcast({'type': 'GAME_START', 'grid_size': GRID_SIZE, 'players': names})
        self.broadcast_turn()

    def broadcast_turn(self):
        current_sock = self.clients[self.turn_index]
        current_id = self.player_data[current_sock]['id']
        self.broadcast({'type': 'TURN', 'player_id': current_id})

    def broadcast_scores(self):
        scores = {p['id']: p['score'] for p in self.player_data.values()}
        self.broadcast({'type': 'SCORE_UPDATE', 'scores': scores})

    def end_game(self):
        self.game_over = True
        # Award Tag Points
        for (r, c), pid in self.flags.items():
            if self.board[r][c] == -1:
                for sock, data in self.player_data.items():
                    if data['id'] == pid:
                        data['score'] += 5
        final_scores = {p['id']: p['score'] for p in self.player_data.values()}
        
        # Reveal entire board for clients
        full_board = []
        for r in range(GRID_SIZE):
            row = []
            for c in range(GRID_SIZE):
                val = self.board[r][c]
                if val == -1: row.append("MINE")
                else: row.append(val)
            full_board.append(row)

        self.broadcast({'type': 'GAME_OVER', 'scores': final_scores, 'board_dump': full_board})

    def run(self):
        try:
            raw = sys.stdin.read()
            config = json.loads(raw)
            host = config.get("ip_address", "0.0.0.0")
            if not host: host = "0.0.0.0"
            self.expected_users = config["users"]
        except: return

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        print(s.getsockname()[1])
        sys.stdout.flush()
        s.listen(5)

        while len(self.clients) < self.expected_users:
            c, a = s.accept()
            self.clients.append(c)
            self.player_data[c] = {'score': 0, 'id': '', 'name': ''}
            threading.Thread(target=self.handle_client, args=(c,), daemon=True).start()
        
        while not self.game_over: time.sleep(1)
        time.sleep(2)
        s.close()

if __name__ == "__main__":
    MinesweeperServer().run()