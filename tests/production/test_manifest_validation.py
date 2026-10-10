"""清单摘要一致仍须拒绝镜像错芯片和伪装的应用版本。"""
import hashlib
import json
import struct

import pytest

from scripts.factory.core import FactoryError, validate_manifest
from test_package_flash import package


def replace_segment(path, manifest, kind, offset, transform):
    segment = next(item for item in manifest[kind]['segments'] if item['offset'] == offset)
    image = path.parent / segment['path']
    raw = bytearray(image.read_bytes())
    transform(raw)
    if offset in (0,0x1000,0x10000,0x20000):
        position=24;checksum=0xef
        for _ in range(raw[1]):
            length=struct.unpack_from('<I',raw,position+4)[0];position+=8
            for value in raw[position:position+length]:checksum^=value
            position+=length
        checksum_at=(position//16+1)*16-1
        raw[checksum_at]=checksum
        raw[checksum_at+1:checksum_at+33]=hashlib.sha256(raw[:checksum_at+1]).digest()
    image.write_bytes(raw)
    segment.update(sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))
    path.write_text(json.dumps(manifest), encoding='utf-8')


def test_grbl_rejects_s3_image_even_if_manifest_hash_matches(tmp_path):
    recipe, path, manifest = package(tmp_path)
    replace_segment(path, manifest, 'grbl', 0x10000, lambda raw: raw.__setitem__(12, 9))
    with pytest.raises(FactoryError):
        validate_manifest(path, recipe)


@pytest.mark.parametrize(('kind', 'offset'), [('s3', 0), ('grbl', 0x1000)])
def test_invalid_bootloader_not_accepted_as_complete_package(tmp_path, kind, offset):
    recipe, path, manifest = package(tmp_path)
    replace_segment(path, manifest, kind, offset, lambda raw: raw.__setitem__(0, 0))
    with pytest.raises(FactoryError):
        validate_manifest(path, recipe)


def test_version_string_outside_app_descriptor_cannot_validate_s3(tmp_path):
    recipe, path, manifest = package(tmp_path)

    def alter(raw):
        raw[32:80] = bytes(48)
        raw[160:174]=b'hutuji.1.0.36\0'

    replace_segment(path, manifest, 's3', 0x20000, alter)
    with pytest.raises(FactoryError):
        validate_manifest(path, recipe)
