import socket
import sys
import json

def main():
    # Update usage to require ID and Username as arguments
    if len(sys.argv) < 5:
        print("Usage: python client.py <server_ip> <server_port> <user_id> <username>")
        sys.exit(1)

    server_ip = sys.argv[1]
    server_port = int(sys.argv[2])
    
    # Parse the new parameters
    try:
        user_id = int(sys.argv[3])
    except ValueError:
        print("Error: user_id must be an integer.")
        sys.exit(1)
        
    username = sys.argv[4]

    # Create the config object manually from parameters
    user_config = {
        "userId": user_id,
        "username": username
    }

    # Start Connection
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    try:
        client_socket.connect((server_ip, server_port))
    except ConnectionRefusedError:
        print("Could not connect to server.")
        sys.exit(1)

    print(f"Connected as {username} (ID: {user_id})")
    print("Type 'exit' or 'quit' at any time to leave the game.")
    
    # Handshake: Send our identity
    client_socket.sendall(json.dumps(user_config).encode())

    rounds_played = 0
    MAX_ROUNDS = 3

    # Game Loop
    while True:
        try:
            # Receive data from server
            data = client_socket.recv(4096).decode()
            if not data:
                print("\nServer closed connection.")
                break
            
            # Print server message (e.g., "Please enter your move...")
            print(data, end="")

            # Check if a round finished (based on server outcome message)
            if "Result:" in data:
                rounds_played += 1
                if rounds_played >= MAX_ROUNDS:
                    print(f"\nGame Over: {MAX_ROUNDS} rounds played.")
                    break

            # Simple heuristic: If the server is asking for a move, we read from keyboard
            if "move" in data.lower():
                # input() works normally here because we didn't pipe stdin
                move = input()
                
                # Check for exit command
                if move.strip().lower() in ['exit', 'quit']:
                    print("Exiting game...")
                    break

                client_socket.sendall(move.encode())
                print("Waiting for opponent...")
                
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"\nError: {e}")
            break

    client_socket.close()

if __name__ == "__main__":
    main()