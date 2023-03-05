from cancelchain.config import EnvAppSettings


def test_environ_settings():
    s = EnvAppSettings.from_env()
    assert s.SECRET_KEY == 'testkey'
    assert [
        'CCB9JajrPayCVUqRU7RrDAVfZ1QPj135moCyrKkNwMwEtRCC',
        'CC3QfbBDAEktCNPzcTg8DPz4a1qY5zMKvenQjr5nFoaKXaCC'
    ] == s.READER_ADDRESSES
