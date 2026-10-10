#!/usr/bin/env python3
"""Add the existing SwissJob frontend to a configured compose, once."""

from __future__ import annotations

import sys
from pathlib import Path


FRONTEND_BLOCK = """  frontend:
    image: swissjob-frontend:point5-0000000
    mem_limit: 128m
    container_name: swissjob-frontend
    environment:
      SWISSJOB_BACKEND_HOST: ${SWISSJOB_BACKEND_HOST:-swissjob-backend}
    ports:
    - target: 80
      published: '4000'
      protocol: tcp
      mode: ingress
    restart: unless-stopped
    networks:
      swissjob_swissjob-net:
        aliases:
        - swissjob-frontend
        - frontend
    healthcheck:
      test:
      - CMD-SHELL
      - wget -q -O- http://127.0.0.1/nginx-health || exit 1
      interval: 30s
      timeout: 5s
      retries: 3
    logging:
      driver: json-file
      options:
        max-file: '10'
        max-size: 10m
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
"""


def ensure(path: Path) -> bool:
    data = path.read_text()
    if "  frontend:\n" in data:
        return False
    marker = "\nnetworks:\n"
    if marker not in data:
        raise ValueError("compose sin bloque networks de nivel superior")
    path.write_text(data.replace(marker, "\n" + FRONTEND_BLOCK + marker, 1))
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"uso: {sys.argv[0]} COMPOSE")
    ensure(Path(sys.argv[1]))
