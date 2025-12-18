import sys
import socket
import threading
import tkinter as tk
from tkinter import messagebox

class BattleshipClient:
    def __init__(self, ip, port, user_id, name):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.running = True
        self.my_turn = False
        
        # --- GUI Setup ---
        self.root = tk.Tk()
        self.root.title(f"Battleship - {name}")
        self.root.geometry("400x500")

        # Info Label
        self.lbl_info = tk.Label(self.root, text="Connecting...", font=("Arial", 14), pady=10)
        self.lbl_info.pack()

        # Game Grid (5x5)
        self.grid_frame = tk.Frame(self.root)
        self.grid_frame.pack(pady=20)
        
        self.buttons = {} # Key: (r,c), Value: Button
        for r in range(5):
            for c in range(5):
                btn = tk.Button(self.grid_frame, text="~", width=6, height=3,
                                command=lambda r=r, c=c: self.on_click(r, c))
                btn.grid(row=r, column=c)
                self.buttons[(r, c)] = btn

        # Exit Entry (as requested)
        self.cmd_frame = tk.Frame(self.root)
        self.cmd_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
        tk.Label(self.cmd_frame, text="Type 'exit' to quit:").pack(side=tk.LEFT)
        self.entry_cmd = tk.Entry(self.cmd_frame)
        self.entry_cmd.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.entry_cmd.bind("<Return>", self.check_exit_command)

        # Connect
        try:
            self.sock.connect((ip, int(port)))
            self.sock.send(f"{user_id}:{name}".encode())
            threading.Thread(target=self.listen_server, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Error", f"Could not connect: {e}")
            sys.exit(1)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.disable_all_buttons()

    def check_exit_command(self, event):
        if self.entry_cmd.get().strip().lower() == "exit":
            self.on_close()

    def disable_all_buttons(self):
        for btn in self.buttons.values():
            if btn['state'] != tk.DISABLED:
                btn.config(state=tk.DISABLED)

    def enable_valid_buttons(self):
        # Enable buttons that haven't been clicked yet (text is "~")
        for btn in self.buttons.values():
            if btn['text'] == "~":
                btn.config(state=tk.NORMAL)

    def on_click(self, r, c):
        if self.my_turn:
            self.sock.send(f"ATTACK:{r},{c}".encode())
            self.my_turn = False
            self.disable_all_buttons()
            self.lbl_info.config(text="Firing...")

    def listen_server(self):
        buffer = ""
        while self.running:
            try:
                data = self.sock.recv(1024).decode()
                if not data: break
                buffer += data
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    self.process_message(line)
            except:
                break

    def process_message(self, msg):
        # Update GUI must be done in main thread, use simple logic here assuming thread-safety for Tkinter (works on most modern OS, if not use root.after)
        if msg.startswith("TURN:YOUR"):
            self.my_turn = True
            self.lbl_info.config(text="YOUR TURN: Attack!")
            self.enable_valid_buttons()
        
        elif msg.startswith("TURN:WAIT"):
            self.my_turn = False
            self.disable_all_buttons()
            self.lbl_info.config(text="Opponent's Turn...")

        elif msg.startswith("RESULT:HIT"):
            # Format: RESULT:HIT:r,c
            _, _, coords = msg.split(":")
            r, c = map(int, coords.split(","))
            self.buttons[(r, c)].config(text="HIT", bg="red", state=tk.DISABLED)

        elif msg.startswith("RESULT:MISS"):
            _, _, coords = msg.split(":")
            r, c = map(int, coords.split(","))
            self.buttons[(r, c)].config(text="MISS", bg="gray", state=tk.DISABLED)
        
        elif msg.startswith("INFO:"):
            self.lbl_info.config(text=msg.split(":", 1)[1])

        elif msg.startswith("GAMEOVER"):
            result = msg.split(":")[1]
            messagebox.showinfo("Game Over", result)
            self.on_close()

    def on_close(self):
        self.running = False
        try:
            self.sock.send("EXIT".encode())
            self.sock.close()
        except:
            pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: client.py <IP> <Port> <ID> <Name>")
        sys.exit(1)
    
    BattleshipClient(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]).run()