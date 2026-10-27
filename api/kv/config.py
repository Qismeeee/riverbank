from dataclasses import dataclass

import config

CONNECTION_OPTIONS = {"decode_responses": True, "health_check_interval": 30}


class ValkeyClientError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ValkeySettings:
    host: str
    port: int = 6379
    db: int = 0
    username: str = ""
    password: str = ""
    ssl: bool = False
    key_prefix: str = "riverbank:"
    timeout: float = 5.0

    def connection(self) -> dict:
        if not self.host:
            raise ValkeyClientError("VALKEY_NOT_CONFIGURED")
        options = {
            "host": self.host,
            "port": self.port,
            "db": self.db,
            "ssl": self.ssl,
            "socket_timeout": self.timeout,
            "socket_connect_timeout": self.timeout,
        }
        if self.username:
            options["username"] = self.username
        if self.password:
            options["password"] = self.password
        return options | CONNECTION_OPTIONS


def valkey_settings() -> ValkeySettings:
    return ValkeySettings(
        config.VALKEY_HOST,
        config.VALKEY_PORT,
        config.VALKEY_DB,
        config.VALKEY_USERNAME,
        config.VALKEY_PASSWORD,
        config.VALKEY_SSL,
        config.VALKEY_KEY_PREFIX,
        config.VALKEY_TIMEOUT_SECONDS,
    )
