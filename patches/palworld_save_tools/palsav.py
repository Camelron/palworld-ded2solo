import ctypes
import os
import zlib

MAGIC_BYTES = b"PlZ"

_OOZ_DLL_PATH = os.environ.get("PALWORLD_OOZ_DLL_PATH")
_ooz_lib = None


def _get_ooz_lib():
    global _ooz_lib
    if _ooz_lib is None:
        if not _OOZ_DLL_PATH or not os.path.exists(_OOZ_DLL_PATH):
            raise Exception(
                "Oodle-compressed (PlM) save detected but PALWORLD_OOZ_DLL_PATH env var "
                "is not set to a valid libooz.dll path."
            )
        lib = ctypes.CDLL(_OOZ_DLL_PATH)
        lib.Ooz_Decompress.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
        ]
        lib.Ooz_Decompress.restype = ctypes.c_int
        _ooz_lib = lib
    return _ooz_lib


def _ooz_decompress(compressed: bytes, uncompressed_len: int) -> bytes:
    lib = _get_ooz_lib()
    out_buf = ctypes.create_string_buffer(uncompressed_len + 64)
    result = lib.Ooz_Decompress(
        compressed, len(compressed), out_buf, uncompressed_len,
        0, 0, 0, None, 0, None, None, None, 0, 0,
    )
    if result != uncompressed_len:
        raise Exception(f"Ooz_Decompress returned {result}, expected {uncompressed_len}")
    return out_buf.raw[:uncompressed_len]


def decompress_sav_to_gvas(data: bytes) -> tuple[bytes, int]:
    uncompressed_len = int.from_bytes(data[0:4], byteorder="little")
    compressed_len = int.from_bytes(data[4:8], byteorder="little")
    magic_bytes = data[8:11]
    save_type = data[11]
    data_start_offset = 12
    # Check for magic bytes
    if magic_bytes == b"CNK":
        uncompressed_len = int.from_bytes(data[12:16], byteorder="little")
        compressed_len = int.from_bytes(data[16:20], byteorder="little")
        magic_bytes = data[20:23]
        save_type = data[23]
        data_start_offset = 24

    if magic_bytes == b"PlM":
        # Oodle-compressed save (Palworld's 2026 "Summer Update"). Decompressed via
        # libooz (github.com/zao/ooz), a clean-room open-source reimplementation of
        # the Oodle Kraken algorithm — not Epic/RAD's proprietary code.
        compressed_data = data[data_start_offset:]
        if compressed_len != len(compressed_data):
            raise Exception(f"incorrect compressed length: {compressed_len}")
        uncompressed_data = _ooz_decompress(compressed_data, uncompressed_len)
        if uncompressed_len != len(uncompressed_data):
            raise Exception(f"incorrect uncompressed length: {uncompressed_len}")
        return uncompressed_data, save_type

    if magic_bytes != MAGIC_BYTES:
        if (
            magic_bytes == b"\x00\x00\x00"
            and uncompressed_len == 0
            and compressed_len == 0
        ):
            raise Exception(
                f"not a compressed Palworld save, found too many null bytes, this is likely corrupted"
            )
        raise Exception(
            f"not a compressed Palworld save, found {magic_bytes!r} instead of {MAGIC_BYTES!r}"
        )
    # Valid save types
    if save_type not in [0x30, 0x31, 0x32]:
        raise Exception(f"unknown save type: {save_type}")
    # We only have 0x31 (single zlib) and 0x32 (double zlib) saves
    if save_type not in [0x31, 0x32]:
        raise Exception(f"unhandled compression type: {save_type}")
    if save_type == 0x31:
        # Check if the compressed length is correct
        if compressed_len != len(data) - data_start_offset:
            raise Exception(f"incorrect compressed length: {compressed_len}")
    # Decompress file
    uncompressed_data = zlib.decompress(data[data_start_offset:])
    if save_type == 0x32:
        # Check if the compressed length is correct
        if compressed_len != len(uncompressed_data):
            raise Exception(f"incorrect compressed length: {compressed_len}")
        # Decompress file
        uncompressed_data = zlib.decompress(uncompressed_data)
    # Check if the uncompressed length is correct
    if uncompressed_len != len(uncompressed_data):
        raise Exception(f"incorrect uncompressed length: {uncompressed_len}")

    return uncompressed_data, save_type


def compress_gvas_to_sav(data: bytes, save_type: int) -> bytes:
    # Always writes back using plain zlib (PlZ) rather than Oodle — the community
    # (github.com/cheahjs/palworld-save-tools/issues/214) confirmed Palworld still
    # accepts zlib-recompressed saves even though it originally wrote Oodle-compressed ones.
    uncompressed_len = len(data)
    compressed_data = zlib.compress(data)
    compressed_len = len(compressed_data)
    if save_type == 0x32:
        compressed_data = zlib.compress(compressed_data)

    # Create a byte array and append the necessary information
    result = bytearray()
    result.extend(uncompressed_len.to_bytes(4, byteorder="little"))
    result.extend(compressed_len.to_bytes(4, byteorder="little"))
    result.extend(MAGIC_BYTES)
    result.extend(bytes([save_type]))
    result.extend(compressed_data)

    return bytes(result)
