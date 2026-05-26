"""External Payload Storage for Temporal.

Pattern-C hardening checklist: "payloads >10 KB routed to External
Payload Storage (S3 driver)".

The Codec serializes the payload to S3 when it exceeds threshold; the
inline payload carries only the s3:// reference. The reverse path
fetches and reinflates.

Wiring (see src/worker.py):
    import dataclasses
    from temporalio.converter import DataConverter
    converter = dataclasses.replace(
        DataConverter.default,
        payload_codec=S3PayloadCodec(bucket=os.environ["AWS_S3_BUCKET"]),
    )
    Client.connect(target, data_converter=converter)
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Sequence

from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec


_S3_ENCODING = b"binary/s3"


class S3PayloadCodec(PayloadCodec):
    def __init__(
        self,
        *,
        bucket: str,
        threshold_bytes: int = 10_000,
        prefix: str = "payloads",
        s3_client: Any | None = None,
    ) -> None:
        """Construct the codec.

        `s3_client` is injectable for tests; otherwise the default
        boto3.client("s3") is created lazily on first encode/decode."""
        self._bucket = bucket
        self._threshold = threshold_bytes
        self._prefix = prefix
        self._client = s3_client

    def _s3(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out: list[Payload] = []
        for p in payloads:
            if len(p.data) <= self._threshold:
                out.append(p)
                continue
            key = f"{self._prefix}/{uuid.uuid4().hex}.bin"
            self._s3().put_object(Bucket=self._bucket, Key=key, Body=p.data)
            new_meta = dict(p.metadata)
            new_meta["encoding"] = _S3_ENCODING
            out.append(
                Payload(
                    metadata=new_meta,
                    data=json.dumps(
                        {"bucket": self._bucket, "key": key}
                    ).encode(),
                )
            )
        return out

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        out: list[Payload] = []
        for p in payloads:
            if p.metadata.get("encoding") != _S3_ENCODING:
                out.append(p)
                continue
            ref = json.loads(p.data.decode())
            obj = self._s3().get_object(Bucket=ref["bucket"], Key=ref["key"])
            data = obj["Body"].read()
            new_meta = {k: v for k, v in p.metadata.items() if k != "encoding"}
            out.append(Payload(metadata=new_meta, data=data))
        return out
