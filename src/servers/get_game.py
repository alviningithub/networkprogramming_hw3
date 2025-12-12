import os

def get_game_location(self, user_id, game_name, version=None):
        """
        Helper to construct the path for a game or a specific version.
        Structure: storage_dir / userId / gameName / [version]
        """
        path = os.path.join(self.storage_dir, str(user_id), game_name)
        if version:
            path = os.path.join(path, version)
        return path