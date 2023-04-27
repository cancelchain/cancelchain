import json
import os
from dataclasses import dataclass, field, fields
from typing import ClassVar


@dataclass
class EnvironSettings:
    _prefix: ClassVar[str] = ''

    @classmethod
    def getenv(cls, name):
        return os.environ.get(f'{cls._prefix}{name}')

    @classmethod
    def from_env(cls):
        c = cls()
        for f in fields(c):
            if (v := cls.getenv(f.name)) is not None:
                v = v.strip()
                try:
                    setattr(c, f.name, json.loads(v))
                except Exception:
                    setattr(c, f.name, v)
        return c


@dataclass
class EnvAppSettings(EnvironSettings):
    _prefix: ClassVar[str] = 'CC_'

    SECRET_KEY: str = field(default=None)
    SQLALCHEMY_DATABASE_URI: str = field(default=None)
    CACHE_TYPE: str = field(default='NullCache')
    CELERY_BROKER_URL: str = field(default=None)
    NODE_HOST: str = field(default=None)
    PEERS: list[str] = field(default_factory=list)
    API_CLIENT_TIMEOUT: int = field(default=10)
    API_ASYNC_PROCESSING: bool = field(default=False)
    DEFAULT_COMMAND_HOST: str = field(default=None)
    WALLET_DIR: str = field(default=None)
    ADMIN_ADDRESSES: list[str] = field(default_factory=list)
    MILLER_ADDRESSES: list[str] = field(default_factory=list)
    TRANSACTOR_ADDRESSES: list[str] = field(default_factory=list)
    READER_ADDRESSES: list[str] = field(default_factory=list)
