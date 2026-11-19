"""Connections, one per region, resolved from Secrets Manager.

There is deliberately no function that opens both regions at once. The router
picks a region and gets that connection; code that wanted to join across them
would have to be written on purpose, and would be visible in review.
"""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from functools import lru_cache

import boto3
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector

from .config import AWS_REGION, REGIONS, secret_name


@lru_cache(maxsize=4)
def _creds(region: str) -> dict:
    if region not in REGIONS:
        raise ValueError(f"unknown data region {region!r}; expected one of {REGIONS}")
    sm = boto3.client("secretsmanager", region_name=AWS_REGION)
    return json.loads(sm.get_secret_value(SecretId=secret_name(region))["SecretString"])


def dsn(region: str) -> str:
    c = _creds(region)
    host = os.environ.get(f"RAMCO_DB_HOST_{region}", c.get("host"))
    return (f"host={host} port={c.get('port', 5432)} dbname={c.get('dbname', 'ramco')} "
            f"user={c['username']} password={c['password']} sslmode=require")


@contextmanager
def connect(region: str, autocommit: bool = True, vectors: bool = True):
    """`vectors=False` is for the very first connection, which has to create
    the extension that the vector adapter needs in order to register."""
    with psycopg.connect(dsn(region), autocommit=autocommit, row_factory=dict_row) as conn:
        if vectors:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
        yield conn
