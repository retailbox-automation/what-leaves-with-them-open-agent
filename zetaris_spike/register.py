"""Register the three synthetic sources in a local Zetaris (idempotent: "already exists" is skipped).

Needs: wlwt-chat-pg on 127.0.0.1:55432 with table chat (CHATPG_PASSWORD in the environment),
docs_api.py on 127.0.0.1:18090, tickets.csv copied into the driver's customer-data volume.
Containers reach the host as host.docker.internal (Docker Desktop). Statements are not printed
(one of them carries the chat database password).

Usage: python3 -m zetaris_spike.register
"""
import os

from .zclient import Zetaris

TICKETS_PATH = "/home/zetaris/customer-data/shared/wlwt/tickets.csv"
DOCS_URL = "http://host.docker.internal:18090/docs"


def statements(chat_password):
    return [
        ("chat datasource",
         'CREATE DATASOURCE WLWT_CHAT DESCRIBE BY "WLWT synthetic team chat in Postgres" OPTIONS ('
         'jdbcdriver "org.postgresql.Driver", jdbcurl "jdbc:postgresql://host.docker.internal:55432/wlwt", '
         f'username "wlwt", password "{chat_password}", fetchsize "1000")'),
        ("chat table", 'REGISTER DATASOURCE TABLE "chat" FROM WLWT_CHAT'),
        ("docs database", 'CREATE LIGHTNING DATABASE WLWT_DOCS DESCRIBE BY "WLWT synthetic docs API"'),
        ("docs REST table",
         f'CREATE LIGHTNING REST TABLE docs_raw FROM WLWT_DOCS REQUEST(endpoint "{DOCS_URL}", method "get", '
         'response_type "json", http_encoding "URLENCODED") HEADER () BODY ()'),
        ("tickets database", 'CREATE LIGHTNING DATABASE WLWT_TICKETS DESCRIBE BY "WLWT synthetic support tickets CSV"'),
        ("tickets CSV table",
         f'CREATE LIGHTNING FILESTORE TABLE tickets FROM WLWT_TICKETS FORMAT CSV OPTIONS (PATH "{TICKETS_PATH}", '
         'header "true", inferSchema "true", multiLine "true", escape "\\"")'),
    ]


def main():
    password = os.environ["CHATPG_PASSWORD"]
    z = Zetaris()
    for name, statement in statements(password):
        try:
            z.run(statement)
            print(name, "created")
        except RuntimeError as e:
            msg = str(e).replace(password, "***")
            print(name, "exists" if "exist" in msg.lower() else "ERROR " + msg[:300])


if __name__ == "__main__":
    main()
