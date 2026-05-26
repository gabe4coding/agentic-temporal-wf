"""In-memory fake-S3 tests for the codec. We avoid moto here so the
test suite stays lightweight; the fake exposes the two boto3 calls the
codec uses (put_object, get_object)."""
from __future__ import annotations

import io

import pytest
from temporalio.api.common.v1 import Payload

from src.payload_storage.s3_codec import S3PayloadCodec


class _FakeS3:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket, Key, Body):  # boto3 keyword API
        self.store[(Bucket, Key)] = Body
        return {}

    def get_object(self, *, Bucket, Key):
        return {"Body": io.BytesIO(self.store[(Bucket, Key)])}


@pytest.mark.asyncio
async def test_small_payload_inline():
    fake = _FakeS3()
    codec = S3PayloadCodec(
        bucket="b", threshold_bytes=10_000, s3_client=fake
    )
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b"hello")
    enc = await codec.encode([payload])
    assert enc[0].data == b"hello"
    assert enc[0].metadata.get("encoding") == b"json/plain"
    # S3 should not have been touched.
    assert fake.store == {}


@pytest.mark.asyncio
async def test_large_payload_spills_to_s3():
    fake = _FakeS3()
    codec = S3PayloadCodec(bucket="b", threshold_bytes=10, s3_client=fake)
    big = b"x" * 1000
    payload = Payload(metadata={"encoding": b"json/plain"}, data=big)
    enc = await codec.encode([payload])
    assert enc[0].metadata.get("encoding") == b"binary/s3"
    assert b"bucket" in enc[0].data and b"key" in enc[0].data
    # Round-trip
    dec = await codec.decode(enc)
    assert dec[0].data == big
    assert dec[0].metadata.get("encoding") == b"json/plain"


@pytest.mark.asyncio
async def test_decode_passes_through_non_s3_payloads():
    fake = _FakeS3()
    codec = S3PayloadCodec(bucket="b", threshold_bytes=10, s3_client=fake)
    payload = Payload(metadata={"encoding": b"json/plain"}, data=b"untouched")
    out = await codec.decode([payload])
    assert out[0].data == b"untouched"
