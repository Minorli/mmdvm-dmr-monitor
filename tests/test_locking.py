import pytest

from backend.locking import DataDirectoryLock


def test_two_process_owners_cannot_share_data_directory(tmp_path):
    with DataDirectoryLock(tmp_path), pytest.raises(RuntimeError), DataDirectoryLock(tmp_path):
        pass
    with DataDirectoryLock(tmp_path):
        pass
