import socket
import struct
import os
from dotenv import load_dotenv
from typing import Tuple, Optional
import json

load_dotenv()
token=os.getenv("TOKEN") 


def create_tcp_passive_socket(host: str, port: int, backlog: int = 5) -> socket.socket:
    """
    Create a TCP passive (listening) socket.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(backlog)
    return s


def create_tcp_socket(host : str, port: int ) -> socket.socket:
    """
    Create a normal active TCP socket.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host,port))
    return s


def send_json(sock: socket.socket, obj: dict) -> None:
    """
    Send a dictionary as JSON using Length-Prefixed Framing Protocol.
    """
    obj["token"] = token

    data = json.dumps(obj).encode("utf-8")
    length_prefix = struct.pack("!I", len(data))
    sock.sendall(length_prefix + data)


class ConnectionClosedByPeer(Exception):
    pass

def recv_json(sock: socket.socket, timeout: float | None  = None) -> dict | None:
    """
    Receive a JSON dictionary using length-prefixed framing.
    Returns None if timeout or invalid JSON.
    Raises ConnectionClosedByPeer if the socket is closed by peer.
    """
    sock.settimeout(timeout)
    
    # Read 4-byte length prefix
    prefix = b""
    try:
        while len(prefix) < 4:
            chunk = sock.recv(4 - len(prefix))
            if not chunk:
                raise ConnectionClosedByPeer()
            prefix += chunk
    except socket.timeout:
        return None

    msg_len = struct.unpack("!I", prefix)[0]

    # Read the message
    data = b""
    try:
        while len(data) < msg_len:
            chunk = sock.recv(msg_len - len(data))
            if not chunk:
                raise ConnectionClosedByPeer()
            data += chunk
    except socket.timeout:
        return None

    try:
        result = json.loads(data.decode("utf-8"))
    except Exception:
        return None

    return result

def send_file(sock: socket.socket, file_path: str, data: dict) -> None:
    """
    Send a file + metadata.
    Protocol: [Length-Prefixed JSON Header (with filesize)] + [Raw File Bytes]
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    filesize = os.path.getsize(file_path)
    
    # Add file details to the metadata
    data["filename"] = os.path.basename(file_path)
    data["filesize"] = filesize
    
    # 1. Send the JSON Header
    # This will attach the token and handle the length prefix automatically
    send_json(sock, data)

    # 2. Send the Raw File Body
    with open(file_path, "rb") as f:
        # sendfile is more efficient than reading/writing in a loop in userspace
        sock.sendfile(f)


def recv_file(sock: socket.socket, save_dir: str, timeout: float | None = None) -> Tuple[dict | None, str | None]:
    """
    Receive a JSON header followed by a binary file.
    Returns: (metadata_dict, saved_file_path)
    """
    # 1. Receive the JSON Header
    metadata = recv_json(sock, timeout)
    
    if metadata is None:
        return None, None
    
    # Check if this message actually contains a file
    filesize = metadata.get("filesize")
    filename = metadata.get("filename")
    
    if filesize is None or filename is None:
        # It might be a regular message without a file
        return metadata, None

    # 2. Receive the File Body
    # Ensure the save directory exists
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, filename)
    
    sock.settimeout(timeout)
    remaining = filesize
    
    try:
        with open(file_path, "wb") as f:
            while remaining > 0:
                # Read in chunks (e.g., 4KB) to avoid memory overload on large files
                chunk_size = 4096 if remaining > 4096 else remaining
                chunk = sock.recv(chunk_size)
                
                if not chunk:
                    raise ConnectionClosedByPeer("Socket closed during file transfer")
                
                f.write(chunk)
                remaining -= len(chunk)
    except socket.timeout:
        return None, None
    except Exception as e:
        # Clean up partial file if error occurs
        if os.path.exists(file_path):
            os.remove(file_path)
        raise e

    return metadata, file_path