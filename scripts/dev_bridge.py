"""Local signing bridge, for development and for taking screenshots.

The Function URL is set to AWS_IAM because this account's guardrails refuse a
public one. The Next.js route cannot sign a request without AWS credentials,
and credentials do not belong on Vercel, so for local work this bridge sits
in between: it listens on 127.0.0.1, signs with whatever credentials the
developer's shell already has, and forwards.

It is deliberately not part of the deployment. Nothing in the production
path imports it, it binds to loopback only, and it exists so the UI can be
driven against the real deployed pipeline without inventing a second code
path through the app.
"""
from __future__ import annotations
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

TARGET = os.environ["PILOT_FUNCTION_URL"]
KEY = os.environ["PILOT_API_KEY"]
REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
_session = boto3.Session()


class Bridge(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):                                  # noqa: N802
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        req = AWSRequest(method="POST", url=TARGET, data=body,
                         headers={"content-type": "application/json",
                                  "x-pilot-key": KEY})
        SigV4Auth(_session.get_credentials(), "lambda", REGION).add_auth(req)
        out = urllib.request.Request(TARGET, data=body,
                                     headers=dict(req.headers), method="POST")
        try:
            with urllib.request.urlopen(out, timeout=90) as resp:
                payload, status = resp.read(), resp.status
        except Exception as exc:                        # noqa: BLE001
            payload = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
            status = 502
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):                          # quiet
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8787"))
    print(f"signing bridge on http://127.0.0.1:{port} -> {TARGET}", flush=True)
    HTTPServer(("127.0.0.1", port), Bridge).serve_forever()
