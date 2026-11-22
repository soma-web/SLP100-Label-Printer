"""SLP command set of the Seiko/SII Smart Label Printer 100.

Command bytes derived from LPrint's lprint-sii.c (c) Michael R Sweet,
Apache License 2.0. See NOTICE.
"""

from PIL import Image, ImageOps

CMD_NOP = 0x00
CMD_STATUS = 0x01
CMD_PRINT = 0x04       # + n + n bytes bitmap (MSB = leftmost dot, 1 = black)
CMD_MARGIN = 0x06      # + n
CMD_LINEFEED = 0x0A
CMD_VERTTAB = 0x0B     # + n blank lines
CMD_FORMFEED = 0x0C    # eject to next label
CMD_DENSITY = 0x0E     # + 0..3
CMD_FINEMODE = 0x17    # + 0/1
# CMD_RESET (0x0F) is deliberately unused: in our tests the printer stopped
# accepting data after it.

DOTS = 192             # print head width in dots (24 mm)
DPI = 203
ROW_BYTES = DOTS // 8


def _skip_blanks(out: bytearray, blanks: int):
    while blanks:
        n = min(blanks, 255)
        out += bytes([CMD_VERTTAB, n]) if n > 1 else bytes([CMD_LINEFEED])
        blanks -= n


def to_job(bw: Image.Image, density: int = 1, fine: bool = True) -> bytes:
    """Convert a 1-bit label image (width = length, height = DOTS) to SLP bytes."""
    if bw.mode != "1" or bw.height != DOTS:
        raise ValueError("label image must be 1-bit and 192 dots high")
    # Each row of the rotated picture is one line of the print head.
    rot = bw.transpose(Image.Transpose.ROTATE_270)
    raw = ImageOps.invert(rot.convert("L")).convert("1", dither=Image.Dither.NONE).tobytes()
    out = bytearray([CMD_MARGIN, 0, CMD_DENSITY, max(0, min(3, int(density))),
                     CMD_FINEMODE, 1 if fine else 0])
    blanks = 0
    for y in range(rot.height):
        row = raw[y * ROW_BYTES:(y + 1) * ROW_BYTES]
        if not any(row):
            blanks += 1
            continue
        _skip_blanks(out, blanks)
        blanks = 0
        out += bytes([CMD_PRINT, ROW_BYTES]) + row
    _skip_blanks(out, blanks)
    out.append(CMD_FORMFEED)
    return bytes(out)


def decode_job(data: bytes) -> list[Image.Image]:
    """Decode SLP bytes back into label images (used by the simulator and tests)."""
    labels, rows, i = [], [], 0
    while i < len(data):
        cmd = data[i]
        i += 1
        if cmd in (CMD_NOP, CMD_STATUS):
            continue
        if cmd in (CMD_MARGIN, CMD_DENSITY, CMD_FINEMODE):
            i += 1
        elif cmd == CMD_PRINT:
            n = data[i]
            row = data[i + 1:i + 1 + n]
            if len(row) != n:
                raise ValueError("truncated raster row")
            rows.append(row.ljust(ROW_BYTES, b"\0"))
            i += 1 + n
        elif cmd == CMD_VERTTAB:
            rows += [bytes(ROW_BYTES)] * data[i]
            i += 1
        elif cmd == CMD_LINEFEED:
            rows.append(bytes(ROW_BYTES))
        elif cmd == CMD_FORMFEED:
            if rows:
                img = Image.frombytes("1", (DOTS, len(rows)), b"".join(rows))
                img = ImageOps.invert(img.convert("L")).convert("1", dither=Image.Dither.NONE)
                labels.append(img.transpose(Image.Transpose.ROTATE_90))
            rows = []
        else:
            raise ValueError(f"unknown command 0x{cmd:02x} at {i - 1}")
    return labels
