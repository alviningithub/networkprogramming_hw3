import pytest
from unittest.mock import Mock, patch, MagicMock
from lobby.DBclient import DatabaseClient


class TestDatabaseClient:
    """Test suite for DatabaseClient class"""

    @pytest.fixture
    def mock_socket(self):
        """Create a mock socket for testing"""
        return Mock()

    @pytest.fixture
    def db_client(self, mock_socket):
        """Create a DatabaseClient instance with mocked socket"""
        with patch('lobby.DBclient.create_tcp_socket', return_value=mock_socket):
            client = DatabaseClient('localhost', 5432)
            return client

    def test_init(self, mock_socket):
        """Test DatabaseClient initialization"""
        with patch('lobby.DBclient.create_tcp_socket', return_value=mock_socket):
            client = DatabaseClient('localhost', 5432)
            assert client.host == 'localhost'
            assert client.port == 5432
            assert client.socket == mock_socket

    def test_connect_db(self, mock_socket):
        """Test database connection"""
        with patch('lobby.DBclient.create_tcp_socket', return_value=mock_socket):
            client = DatabaseClient('localhost', 5432)
            assert client.socket is not None

    def test_insert_user(self, db_client):
        """Test insert_user method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [1]}
            result = db_client.insert_user('alice', 'hash123', 'player')
            assert result == [1]
            mock_send.assert_called_once()

    def test_insert_user_error(self, db_client):
        """Test insert_user with error"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "error", "error": "User already exists"}
            with pytest.raises(Exception):
                db_client.insert_user('alice', 'hash123', 'player')

    def test_find_user_by_name_and_password(self, db_client):
        """Test find_user_by_name_and_password method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'alice', 'hash123', 'online', 'player']]}
            result = db_client.find_user_by_name_and_password('alice', 'hash123')
            assert result == [[1, 'alice', 'hash123', 'online', 'player']]

    def test_find_user_by_name_and_password_not_found(self, db_client):
        """Test find_user_by_name_and_password when user not found"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": []}
            result = db_client.find_user_by_name_and_password('nonexistent', 'hash123')
            assert result == []

    def test_find_user_by_id(self, db_client):
        """Test find_user_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'alice', 'hash123', 'online', 'player']]}
            result = db_client.find_user_by_id(1)
            assert result == [[1, 'alice', 'hash123', 'online', 'player']]

    def test_update_user(self, db_client):
        """Test update_user method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [1]}
            result = db_client.update_user(1, name='alice_new', status='offline')
            assert result == [1]
            assert mock_send.called

    def test_update_user_no_fields(self, db_client):
        """Test update_user with no fields raises error"""
        with pytest.raises(ValueError):
            db_client.update_user(1)

    def test_list_online_users(self, db_client):
        """Test list_online_users method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'alice', 'hash123', 'online', 'player']]}
            result = db_client.list_online_users()
            assert len(result) > 0

    def test_create_room(self, db_client):
        """Test create_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.side_effect = [
                {"status": "ok", "data": [[1]]},  # Room creation response
                {"status": "ok", "data": [[1, 1, 1]]}  # Add user to room response
            ]
            result = db_client.create_room('Game Room 1', 1, 'public', 'idle', 1)
            assert result == [[1]]

    def test_create_room_error(self, db_client):
        """Test create_room with error"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "error", "error": "Game not found"}
            with pytest.raises(Exception):
                db_client.create_room('Game Room 1', 1, 'public', 'idle', 999)

    def test_check_user_in_room(self, db_client):
        """Test check_user_in_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.check_user_in_room(1)
            assert result == [[1]]

    def test_add_user_to_room(self, db_client):
        """Test add_user_to_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.add_user_to_room(1, 2)
            assert result == [[1]]

    def test_leave_room(self, db_client):
        """Test leave_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.leave_room(2)
            assert result == [[1]]

    def test_list_user_in_room(self, db_client):
        """Test list_user_in_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'alice'], [2, 'bob']]}
            result = db_client.list_user_in_room(1)
            assert len(result) == 2
            assert result[0][1] == 'alice'

    def test_get_room_by_id(self, db_client):
        """Test get_room_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'Game Room 1', 1, 'public', 'idle', 1]]}
            result = db_client.get_room_by_id(1)
            assert len(result) > 0

    def test_get_room_by_id_with_status(self, db_client):
        """Test get_room_by_id with visibility filter"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'Game Room 1', 1, 'public', 'idle', 1]]}
            result = db_client.get_room_by_id(1, status='public')
            assert len(result) > 0

    def test_delete_room(self, db_client):
        """Test delete_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.delete_room(1)
            assert result == [[1]]

    def test_update_room(self, db_client):
        """Test update_room method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'Updated Room', 1, 'private', 'idle', 1]]}
            result = db_client.update_room(1, name='Updated Room', visibility='private')
            assert len(result) > 0

    def test_update_room_no_fields(self, db_client):
        """Test update_room with no fields raises error"""
        with pytest.raises(ValueError):
            db_client.update_room(1)

    def test_add_invite(self, db_client):
        """Test add_invite method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.add_invite(1, 2, 1)
            assert result == [[1]]

    def test_get_invite_by_id(self, db_client):
        """Test get_invite_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.get_invite_by_id(1)
            assert len(result) > 0

    def test_list_invites(self, db_client):
        """Test list_invites method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 'alice', 1]]}
            result = db_client.list_invites(2)
            assert len(result) > 0

    def test_remove_invite_by_id(self, db_client):
        """Test remove_invite_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_invite_by_id(1)
            assert len(result) > 0

    def test_remove_invite_by_toid(self, db_client):
        """Test remove_invite_by_toid method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_invite_by_toid(2)
            assert len(result) > 0

    def test_remove_invite_by_fromid(self, db_client):
        """Test remove_invite_by_fromid method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_invite_by_fromid(1)
            assert len(result) > 0

    def test_insert_request(self, db_client):
        """Test insert_request method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1]]}
            result = db_client.insert_request(1, 2, 1)
            assert result == [[1]]

    def test_get_request_by_id(self, db_client):
        """Test get_request_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.get_request_by_id(1)
            assert len(result) > 0

    def test_get_request_by_id_with_user(self, db_client):
        """Test get_request_by_id with user filter"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.get_request_by_id(1, user_id=2)
            assert len(result) > 0

    def test_list_requests(self, db_client):
        """Test list_requests method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 'alice', 1]]}
            result = db_client.list_requests(2)
            assert len(result) > 0

    def test_remove_request_by_id(self, db_client):
        """Test remove_request_by_id method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_request_by_id(1)
            assert len(result) > 0

    def test_remove_request_by_userid(self, db_client):
        """Test remove_request_by_userid method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_request_by_userid(1)
            assert len(result) > 0

    def test_remove_request_by_fromid(self, db_client):
        """Test remove_request_by_fromid method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_request_by_fromid(1)
            assert len(result) > 0

    def test_remove_request_by_toid(self, db_client):
        """Test remove_request_by_toid method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 1, 1, 2]]}
            result = db_client.remove_request_by_toid(2)
            assert len(result) > 0

    def test_list_all_rooms(self, db_client):
        """Test list_all_rooms method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": [[1, 'Game Room 1', 1, 'public', 'idle', 1]]}
            result = db_client.list_all_rooms()
            assert len(result) > 0

    def test_execute_raw_sql(self, db_client):
        """Test execute_raw_sql method"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "ok", "data": []}
            result = db_client.execute_raw_sql("SELECT * FROM User WHERE id = ?", [1])
            assert mock_send.called

    def test_delete_room_by_hostid(self, db_client):
        """Test delete_room_by_hostid method"""
        result = db_client.delete_room_by_hostid(1)
        assert result is None  # This method is not implemented

    def test_close(self, db_client):
        """Test close method"""
        db_client.close()
        db_client.socket.close.assert_called_once()

    def test_send_request(self, db_client):
        """Test _send_request internal method"""
        with patch('lobby.DBclient.send_json') as mock_send_json, \
             patch('lobby.DBclient.recv_json') as mock_recv_json:
            mock_recv_json.return_value = {"status": "ok", "data": []}
            result = db_client._send_request("SELECT * FROM User", [])
            assert mock_send_json.called
            assert mock_recv_json.called

    def test_error_handling_consistency(self, db_client):
        """Test that all methods handle errors consistently"""
        with patch.object(db_client, '_send_request') as mock_send:
            mock_send.return_value = {"status": "error", "error": "Database error"}
            
            # Test various methods raise exceptions on error
            test_methods = [
                (lambda: db_client.find_user_by_id(1)),
                (lambda: db_client.find_user_by_name_and_password('user', 'pass')),
                (lambda: db_client.list_all_rooms()),
                (lambda: db_client.list_online_users()),
            ]
            
            for method in test_methods:
                with pytest.raises(Exception):
                    method()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
