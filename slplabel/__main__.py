"""Start the server: python -m slplabel"""

import logging

import uvicorn

from .config import Config
from .service import Service
from .web import create_app


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    service = Service(config)
    log = logging.getLogger("slplabel")
    if not config.password:
        log.warning("SLP_PASSWORD ist nicht gesetzt: jeder im Netz kann drucken und Einstellungen ändern.")
    log.info("Druckerstatus: %s", service.printer.status()["message"])
    uvicorn.run(create_app(service), host=config.host, port=config.port, log_level="warning",
                server_header=False)


if __name__ == "__main__":
    main()
