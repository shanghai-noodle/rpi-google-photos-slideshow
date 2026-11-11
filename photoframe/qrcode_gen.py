"""
Lightweight, zero-dependency ISO/IEC 18004 QR Code Byte-Mode Matrix Generator
(Supports Versions 1 to 4, Error Correction Level L, with automatic version selection).
"""

from typing import List, Tuple

# Version -> (total_codewords, ec_codewords_per_block, byte_capacity_level_L, alignment_centers)
_QR_VERSIONS = {
    1: (26, 7, 17, ()),
    2: (44, 10, 32, (6, 18)),
    3: (70, 15, 53, (6, 22)),
    4: (100, 20, 78, (6, 26)),
}

# Precomputed 15-bit Format Information strings for ECC Level L (01) and Mask Patterns 0..7
# (BCH(15,5) masked with 0x5412 per ISO/IEC 18004 Table C.1)
_FORMAT_BITS_LEVEL_L = (
    0x77C4,  # Mask 0
    0x72F3,  # Mask 1
    0x7DAA,  # Mask 2
    0x789D,  # Mask 3
    0x662F,  # Mask 4
    0x6318,  # Mask 5
    0x6C41,  # Mask 6
    0x6976,  # Mask 7
)


def _gf_mul(x: int, y: int) -> int:
    """Multiply two bytes in GF(2^8) with primitive polynomial 0x11D."""
    res = 0
    while y > 0:
        if y & 1:
            res ^= x
        y >>= 1
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    return res


def _rs_generator_poly(degree: int) -> List[int]:
    """Compute Reed-Solomon generator polynomial of given degree."""
    poly = [1]
    root = 1
    for _ in range(degree):
        next_poly = [0] * (len(poly) + 1)
        for i, coeff in enumerate(poly):
            next_poly[i] ^= coeff
            next_poly[i + 1] ^= _gf_mul(coeff, root)
        poly = next_poly
        root = _gf_mul(root, 2)
    return poly


def _rs_encode(data: List[int], ec_len: int) -> List[int]:
    """Compute `ec_len` Reed-Solomon error-correction codewords for `data`."""
    gen = _rs_generator_poly(ec_len)
    rem = [0] * ec_len
    for b in data:
        factor = b ^ rem[0]
        rem = rem[1:] + [0]
        if factor != 0:
            for i in range(ec_len):
                rem[i] ^= _gf_mul(gen[i + 1], factor)
    return rem


def encode_qr_matrix(text: str) -> List[List[bool]]:
    """
    Encodes `text` (UTF-8 bytes) into a square 2D boolean QR code matrix
    (`True` = black module, `False` = white module, without quiet zone).
    """
    payload = text.encode("utf-8")
    ver = 4
    for v in (1, 2, 3, 4):
        if len(payload) <= _QR_VERSIONS[v][2]:
            ver = v
            break
    else:
        payload = payload[: _QR_VERSIONS[4][2]]

    total_cw, ec_cw, _, align_centers = _QR_VERSIONS[ver]
    data_cw_cap = total_cw - ec_cw

    # Build bit stream: Byte Mode indicator (0100) + 8-bit length + payload bits + terminator
    bits: List[int] = [0, 1, 0, 0]
    for shift in range(7, -1, -1):
        bits.append((len(payload) >> shift) & 1)
    for b in payload:
        for shift in range(7, -1, -1):
            bits.append((b >> shift) & 1)

    max_bits = data_cw_cap * 8
    term_len = min(4, max_bits - len(bits))
    bits.extend([0] * term_len)
    while len(bits) % 8 != 0:
        bits.append(0)

    data_bytes: List[int] = []
    for i in range(0, len(bits), 8):
        val = 0
        for bit in bits[i : i + 8]:
            val = (val << 1) | bit
        data_bytes.append(val)

    pad_Toggle = (0xEC, 0x11)
    pad_idx = 0
    while len(data_bytes) < data_cw_cap:
        data_bytes.append(pad_Toggle[pad_idx % 2])
        pad_idx += 1

    ec_bytes = _rs_encode(data_bytes, ec_cw)
    all_codewords = data_bytes + ec_bytes

    size = 17 + 4 * ver
    modules = [[False] * size for _ in range(size)]
    is_func = [[False] * size for _ in range(size)]

    def set_func(r: int, c: int, dark: bool):
        if 0 <= r < size and 0 <= c < size:
            modules[r][c] = dark
            is_func[r][c] = True

    def draw_finder(top_r: int, left_c: int):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = top_r + dr, left_c + dc
                if 0 <= r < size and 0 <= c < size:
                    in_outer = 0 <= dr <= 6 and 0 <= dc <= 6
                    on_border = dr in (0, 6) or dc in (0, 6)
                    in_inner = 2 <= dr <= 4 and 2 <= dc <= 4
                    set_func(r, c, in_outer and (on_border or in_inner))

    draw_finder(0, 0)
    draw_finder(0, size - 7)
    draw_finder(size - 7, 0)

    # Timing patterns
    for i in range(8, size - 8):
        set_func(6, i, i % 2 == 0)
        set_func(i, 6, i % 2 == 0)

    # Alignment patterns
    if align_centers:
        for ar in align_centers:
            for ac in align_centers:
                if (ar == 6 and ac == 6) or (ar == 6 and ac == size - 7) or (ar == size - 7 and ac == 6):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        dist = max(abs(dr), abs(dc))
                        set_func(ar + dr, ac + dc, dist != 1)

    # Dark module
    set_func(size - 8, 8, True)

    # Reserve format info areas
    for i in range(9):
        if not is_func[8][i]:
            set_func(8, i, False)
        if not is_func[i][8]:
            set_func(i, 8, False)
    for i in range(8):
        set_func(8, size - 1 - i, False)
        set_func(size - 1 - i, 8, False)

    # Place data + EC bits in upward/downward 2-column zigzag
    all_bits: List[int] = []
    for cw in all_codewords:
        for shift in range(7, -1, -1):
            all_bits.append((cw >> shift) & 1)

    bit_idx = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        row_iter = range(size - 1, -1, -1) if upward else range(size)
        for r in row_iter:
            for dc in (0, -1):
                c = col + dc
                if not is_func[r][c]:
                    dark = bool(all_bits[bit_idx]) if bit_idx < len(all_bits) else False
                    bit_idx += 1
                    # Apply Mask 0: (r + c) % 2 == 0
                    if (r + c) % 2 == 0:
                        dark = not dark
                    modules[r][c] = dark
        upward = not upward
        col -= 2

    # Write Format Info for Level L, Mask 0
    fmt = _FORMAT_BITS_LEVEL_L[0]
    for i in range(6):
        modules[8][i] = bool((fmt >> (14 - i)) & 1)
    modules[8][7] = bool((fmt >> 8) & 1)
    modules[8][8] = bool((fmt >> 7) & 1)
    modules[7][8] = bool((fmt >> 6) & 1)
    for i in range(6):
        modules[5 - i][8] = bool((fmt >> (5 - i)) & 1)

    for i in range(8):
        modules[8][size - 1 - i] = bool((fmt >> i) & 1)
    for i in range(7):
        modules[size - 1 - i][8] = bool((fmt >> (14 - i)) & 1)

    return modules
