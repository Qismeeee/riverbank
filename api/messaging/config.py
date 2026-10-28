from dataclasses import dataclass

import config

PRODUCER_OPTIONS = {"acks": "all", "enable_idempotence": True}
CONSUMER_OPTIONS = {"enable_auto_commit": False, "auto_offset_reset": "earliest"}


class KafkaClientError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class KafkaSettings:
    bootstrap_servers: str
    client_id: str = "riverbank-api"
    group_id: str = "riverbank"
    security_protocol: str = "PLAINTEXT"
    sasl_mechanism: str = ""
    username: str = ""
    password: str = ""

    def common(self) -> dict:
        if not self.bootstrap_servers:
            raise KafkaClientError("KAFKA_NOT_CONFIGURED")
        options = {
            "bootstrap_servers": [s.strip() for s in self.bootstrap_servers.split(",")],
            "client_id": self.client_id,
            "security_protocol": self.security_protocol,
        }
        if self.sasl_mechanism:
            options |= {
                "sasl_mechanism": self.sasl_mechanism,
                "sasl_plain_username": self.username,
                "sasl_plain_password": self.password,
            }
        return options

    def producer(self) -> dict:
        return self.common() | PRODUCER_OPTIONS

    def consumer(self) -> dict:
        return self.common() | {"group_id": self.group_id} | CONSUMER_OPTIONS


def kafka_settings() -> KafkaSettings:
    return KafkaSettings(
        config.KAFKA_BOOTSTRAP_SERVERS,
        config.KAFKA_CLIENT_ID,
        config.KAFKA_GROUP_ID,
        config.KAFKA_SECURITY_PROTOCOL,
        config.KAFKA_SASL_MECHANISM,
        config.KAFKA_USERNAME,
        config.KAFKA_PASSWORD,
    )
