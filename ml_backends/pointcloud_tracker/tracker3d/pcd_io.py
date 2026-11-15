"""Point cloud readers: PCD (ascii / binary / binary_compressed) and float32 .bin (KITTI 4 / nuScenes 5)."""

import re
import struct

_TYPES = {('F', 4): 'f', ('F', 8): 'd', ('U', 1): 'B', ('U', 2): 'H', ('U', 4): 'I',
          ('I', 1): 'b', ('I', 2): 'h', ('I', 4): 'i'}


def lzf_decompress(data, out_len):
    out = bytearray(out_len)
    i = o = 0
    n = len(data)
    while i < n:
        ctrl = data[i]
        i += 1
        if ctrl < 32:
            ctrl += 1
            out[o : o + ctrl] = data[i : i + ctrl]
            i += ctrl
            o += ctrl
        else:
            length = ctrl >> 5
            ref = o - ((ctrl & 0x1F) << 8) - 1
            if length == 7:
                length += data[i]
                i += 1
            ref -= data[i]
            i += 1
            length += 2
            if ref < 0:
                raise ValueError('LZF: invalid back reference')
            for _ in range(length):
                out[o] = out[ref]
                o += 1
                ref += 1
    return bytes(out)


def parse_pcd(data):
    """[(x, y, z, intensity)] from PCD bytes; intensity is 0 when absent. NaN points are dropped."""
    match = re.search(rb'^DATA\s+(\S+)[^\n]*\n', data, re.M)
    if not match:
        raise ValueError('Invalid PCD: no DATA line')
    header = data[: match.start()].decode('ascii', 'replace')
    body = data[match.end() :]
    kind = match.group(1).decode().lower()
    fields_, size, type_, count = [], [], [], []
    points_n = None
    for line in header.splitlines():
        parts = line.split('#')[0].split()
        if not parts:
            continue
        key, vals = parts[0].upper(), parts[1:]
        if key == 'FIELDS':
            fields_ = [v.lower() for v in vals]
        elif key == 'SIZE':
            size = [int(v) for v in vals]
        elif key == 'TYPE':
            type_ = [v.upper() for v in vals]
        elif key == 'COUNT':
            count = [int(v) for v in vals]
        elif key == 'POINTS':
            points_n = int(vals[0])
    count = count or [1] * len(fields_)
    if not all(f in fields_ for f in 'xyz'):
        raise ValueError('PCD has no x/y/z fields')
    ix, iy, iz = fields_.index('x'), fields_.index('y'), fields_.index('z')
    ii = next((fields_.index(n) for n in ('intensity', 'i', 'reflectance') if n in fields_), None)
    fmt_chars = [_TYPES[(t, s)] for t, s in zip(type_, size)]
    points = []
    if kind == 'ascii':
        cols, c = [], 0
        for k in count:
            cols.append(c)
            c += k
        for line in body.decode('ascii', 'replace').splitlines():
            v = line.split()
            if len(v) < c:
                continue
            p = (float(v[cols[ix]]), float(v[cols[iy]]), float(v[cols[iz]]),
                 float(v[cols[ii]]) if ii is not None else 0.0)
            points.append(p)
    elif kind == 'binary':
        fmt = '<' + ''.join(ch * k for ch, k in zip(fmt_chars, count))
        offsets, c = [], 0
        for k in count:
            offsets.append(c)
            c += k
        row = struct.calcsize(fmt)
        n = min(points_n or len(body) // row, len(body) // row)
        for rec in struct.iter_unpack(fmt, body[: n * row]):
            points.append((rec[offsets[ix]], rec[offsets[iy]], rec[offsets[iz]],
                           float(rec[offsets[ii]]) if ii is not None else 0.0))
    elif kind == 'binary_compressed':
        csize, usize = struct.unpack_from('<II', body, 0)
        raw = lzf_decompress(body[8 : 8 + csize], usize)
        n = points_n
        columns, off = [], 0
        for ch, s, k in zip(fmt_chars, size, count):
            columns.append(struct.unpack_from('<' + ch * (n * k), raw, off)[::k])
            off += s * k * n
        inten = columns[ii] if ii is not None else [0.0] * n
        points = list(zip(columns[ix], columns[iy], columns[iz], (float(v) for v in inten)))
    else:
        raise ValueError(f'Unsupported PCD DATA {kind}')
    return [p for p in points if p[0] == p[0] and p[1] == p[1] and p[2] == p[2]]


def parse_bin(data, dims=4):
    floats = struct.unpack('<%df' % (len(data) // 4), data[: len(data) // 4 * 4])
    return [
        (floats[i], floats[i + 1], floats[i + 2], floats[i + 3] if dims >= 4 else 0.0)
        for i in range(0, len(floats) - dims + 1, dims)
    ]


def parse_point_cloud(data, name='', bin_dims=4):
    clean = name.split('?')[0].lower()
    if clean.endswith('.bin'):
        return parse_bin(data, bin_dims)
    return parse_pcd(data)
