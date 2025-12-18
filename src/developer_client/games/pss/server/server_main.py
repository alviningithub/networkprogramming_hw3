import socket
import sys
import json
import threading
import time

# --- Game Logic Constants ---
MOVES = ["paper", "scissors", "stone"]

def determine_winner(move1, move2):
    if move1 == move2:
        return 0 # Draw
    # Logic: Paper > Stone, Stone > Scissors, Scissors > Paper
    if (move1 == "paper" and move2 == "stone") or \
       (move1 == "stone" and move2 == "scissors") or \
       (move1 == "scissors" and move2 == "paper"):
        return 1 # Player 1 wins
    return 2 # Player 2 wins

def handle_game(clients, player_data):
    """
    Main game loop running in a separate thread.
    clients: list of socket objects [p1_sock, p2_sock]
    player_data: list of dicts [{'username':...}, {'username':...}]
    """
    p1, p2 = clients[0], clients[1]
    p1_name = player_data[0].get('username', 'Player 1')
    p2_name = player_data[1].get('username', 'Player 2')

    # Broadcast Welcome
    start_msg = f"Game Start! {p1_name} vs {p2_name}\n"
    p1.sendall(start_msg.encode())
    p2.sendall(start_msg.encode())

    while True:
        try:
            # Request Moves
            request = "Please enter your move (paper, scissors, stone): "
            p1.sendall(request.encode())
            p2.sendall(request.encode())

            # Receive Moves (Blocking)
            # 1024 bytes is enough for short text
            move1 = p1.recv(1024).decode().strip().lower()
            move2 = p2.recv(1024).decode().strip().lower()

            # Handle Disconnects
            if not move1 or not move2:
                break

            # Validate Moves
            if move1 not in MOVES:
                p1.sendall(b"Invalid move. Defaulting to stone.\n")
                move1 = "stone"
            if move2 not in MOVES:
                p2.sendall(b"Invalid move. Defaulting to stone.\n")
                move2 = "stone"

            # Determine Winner
            result = determine_winner(move1, move2)
            
            # Construct Result Message
            round_summary = f"{p1_name} chose {move1}, {p2_name} chose {move2}.\n"
            
            if result == 0:
                outcome = "Result: It's a Draw!\n"
            elif result == 1:
                outcome = f"Result: {p1_name} Wins!\n"
            else:
                outcome = f"Result: {p2_name} Wins!\n"

            full_msg = round_summary + outcome + "-"*20 + "\n"
            
            p1.sendall(full_msg.encode())
            p2.sendall(full_msg.encode())

        except (ConnectionResetError, BrokenPipeError):
            break
            
    # Cleanup
    print("Game ended.")
    p1.close()
    p2.close()

def main():
    # 1. Read config from Standard Input
    try:
        input_data = sys.stdin.read()
        if not input_data:
            return # Handle empty input gracefully
        config = json.loads(input_data)
    except json.JSONDecodeError:
        sys.stderr.write("Invalid JSON format in stdin.\n")
        return

    host = config.get("ip_address", "127.0.0.1")
    # We ignore specific userids validation for simplicity, 
    # but we respect the user count "users": 2
    
    # 2. Create Socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind((host, 0)) # Bind to port 0 (OS chooses free port)
    
    # 3. Output Port and Flush
    port = server_socket.getsockname()[1]
    print(port)
    sys.stdout.flush()

    server_socket.listen(2)
    
    connected_clients = []
    connected_info = []

    # 4. Wait for users to join
    while len(connected_clients) < 2:
        conn, addr = server_socket.accept()
        
        # Simple Handshake: Expect Client to send their JSON info immediately
        try:
            user_info_bytes = conn.recv(1024)
            user_info = json.loads(user_info_bytes.decode())
        except:
            user_info = {"username": f"Unknown_{len(connected_clients)}"}
            
        connected_clients.append(conn)
        connected_info.append(user_info)
        
        conn.sendall(b"Waiting for opponent...\n")

    # 5. Start Game
    # Handle the game in a thread so the main script doesn't lock up
    game_thread = threading.Thread(target=handle_game, args=(connected_clients, connected_info))
    game_thread.start()
    game_thread.join()
    
    server_socket.close()

if __name__ == "__main__":
    main()