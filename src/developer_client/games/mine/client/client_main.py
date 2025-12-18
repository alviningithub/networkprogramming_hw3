import sys
import socket
import json
import threading
import tkinter as tk
from tkinter import messagebox

class MinesweeperClient:
    def __init__(self, ip, port, user_id, name):
        self.ip = ip
        self.port = int(port)
        self.user_id = user_id
        self.name = name
        self.sock = None
        self.running = True
        self.my_turn = False
        
        self.root = tk.Tk()
        self.root.title(f"Minesweeper - {name}")
        self.buttons = {} 
        self.status_label = None
        self.score_label = None
        self.grid_size = 8 

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.ip, self.port))
            msg = {'type': 'CONNECT', 'id': self.user_id, 'name': self.name}
            self.send_json(msg)
            threading.Thread(target=self.listen_to_server, daemon=True).start()
            threading.Thread(target=self.console_input_listener, daemon=True).start()
        except Exception as e:
            print(f"Conn Error: {e}")
            sys.exit(1)

    def send_json(self, data):
        try:
            self.sock.sendall((json.dumps(data) + "\n").encode('utf-8'))
        except: pass

    def console_input_listener(self):
        for line in sys.stdin:
            if line.strip().lower() == "exit":
                self.running = False
                self.sock.close()
                self.root.destroy()
                sys.exit(0)

    def listen_to_server(self):
        buffer = ""
        while self.running:
            try:
                data = self.sock.recv(4096).decode('utf-8') # Increased buffer for flood fill
                if not data: break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line: 
                        self.root.after(0, lambda m=json.loads(line): self.handle_message(m))
            except: break

    def handle_message(self, msg):
        m_type = msg.get('type')
        
        if m_type == 'GAME_START':
            self.grid_size = msg['grid_size']
            self.create_grid()
            self.status_label.config(text="Game Started!")

        elif m_type == 'TURN':
            if msg['player_id'] == self.user_id:
                self.my_turn = True
                self.status_label.config(text="YOUR TURN", fg="green")
            else:
                self.my_turn = False
                self.status_label.config(text=f"Waiting for {msg['player_id']}", fg="black")

        elif m_type == 'UPDATE':
            r, c = msg['r'], msg['c']
            val = msg['val']
            btn = self.buttons.get((r,c))
            
            # Disable button if revealed
            if val == 'MINE':
                btn.config(text="*", bg="red", state="disabled", relief="sunken")
            elif val == 'FLAG':
                btn.config(text="F", bg="yellow") 
                # Note: We don't disable flags, they can be moved theoretically
            else:
                btn.config(text=str(val) if val != 0 else "", bg="lightgrey", state="disabled", relief="sunken")

        elif m_type == 'SCORE_UPDATE':
            scores = msg['scores']
            # Sort scores by value (highest first)
            sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            
            # Format: [ Alice: 10 ]   [ Bob: 5 ]
            formatted_parts = [f"[ {pid}: {score} ]" for pid, score in sorted_scores]
            s_text = "   ".join(formatted_parts)
            
            self.score_label.config(text=s_text, font=("Arial", 12, "bold"), fg="#333")

        elif m_type == 'GAME_OVER':
            # Reveal full board
            board = msg['board_dump']
            for r in range(self.grid_size):
                for c in range(self.grid_size):
                    val = board[r][c]
                    btn = self.buttons[(r,c)]
                    if val == "MINE":
                        btn.config(text="*", bg="red")
                    else:
                        btn.config(text=str(val) if val != 0 else "", bg="white")
            
            winner = max(msg['scores'], key=msg['scores'].get)
            messagebox.showinfo("Game Over", f"Winner: {winner}\nScores: {msg['scores']}")
            self.root.destroy()
            self.running = False

    def on_click(self, r, c, event_type):
        if not self.my_turn: return
        
        # --- BUG FIX: Check if button is already disabled visually ---
        btn = self.buttons[(r,c)]
        if btn['state'] == 'disabled':
            return

        action = 'REVEAL' if event_type == 'left' else 'TAG'
        self.send_json({'type': 'MOVE', 'r': r, 'c': c, 'action': action})

    def create_grid(self):
        for w in self.root.winfo_children(): w.destroy()
        
        top = tk.Frame(self.root)
        top.pack(side=tk.TOP, fill=tk.X)
        self.score_label = tk.Label(top, text="Scores: 0")
        self.score_label.pack()
        self.status_label = tk.Label(top, text="Waiting...", font=("Arial", 10, "bold"))
        self.status_label.pack()

        grid = tk.Frame(self.root)
        grid.pack()
        
        for r in range(self.grid_size):
            for c in range(self.grid_size):
                btn = tk.Button(grid, text="", width=4, height=2)
                btn.bind('<Button-1>', lambda e, r=r, c=c: self.on_click(r, c, 'left'))
                btn.bind('<Button-3>', lambda e, r=r, c=c: self.on_click(r, c, 'right'))
                btn.grid(row=r, column=c)
                self.buttons[(r,c)] = btn

    def run(self):
        self.connect()
        self.root.mainloop()

if __name__ == "__main__":
    if len(sys.argv) < 5: sys.exit(1)
    MinesweeperClient(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]).run()