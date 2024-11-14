# Ninja Gaiden Sigma 2 TMC Importer by Nozomi Miyamori is under the public domain
# and also marked with CC0 1.0. This file is a part of Ninja Gaiden Sigma 2 TMC Importer.

from __future__ import annotations
#from . import sdp1
from typing import NamedTuple
from enum import IntEnum
from operator import indexOf
import struct

class ContainerParser:
    def __init__(self, magic, data, ldata = b''):
        data = memoryview(data).toreadonly()
        ldata = memoryview(ldata).toreadonly()

        (
                magic_, version, metadata_pos,
                container_nbytes, chunk_count, valid_chunk_count,
                offset_table_pos, size_table_pos, sub_container_pos,
        ) = struct.unpack_from('< 8sII III4x III', data)

        magic0 = magic.ljust(8, b'\0')
        if magic_ != magic0:
            raise ValueError(f'unexpected magic bytes {magic_} (expected: {magic0}).')
        if version != 0x0101_0000:
            raise ValueError(f'unexpected verison {version:08x} (expected: 01010000')
        self._data = data[:container_nbytes]

        lcontainer_nbytes = 0
        if metadata_pos == 0x50:
            if not ldata:
                raise ValueError(f'{magic.decode()} should have ldata, but no ldata was passed.')
            lhead = (_, lcontainer_nbytes, _) = struct.unpack_from('< III', data, 0x40)
            lhead_ = struct.unpack_from('< III', ldata)
            if lhead_ != lhead:
                raise ValueError(f'mismatch between {magic.decode()}\'s Lhead in TMC and the one in TMCL: {lhead} != {lhead_}')
        self._ldata = ldata[:lcontainer_nbytes]

        o = metadata_pos
        p = ( offset_table_pos or size_table_pos or sub_container_pos or container_nbytes )
        self.metadata = self._metadata = data[o:p]

        o = offset_table_pos
        p = o + 4*chunk_count*(o > 0)
        offset_table = data[o:p].cast('I')

        o = size_table_pos
        p = o + 4*chunk_count*(o > 0)
        size_table = data[o:p].cast('I')

        o = sub_container_pos
        p = ( offset_table and offset_table[0] or container_nbytes )*(o > 0)
        self.sub_container = self._sub_container = data[o:p]

        self.chunks = self._chunks = tuple(ContainerParser._gen_chunks(
            self._ldata or self._data, offset_table, size_table
        ))

    @staticmethod
    def _gen_chunks(data, offset_table, size_table):
        if size_table:
            yield from ( data[o:o+n] for o, n in zip(offset_table, size_table) )
            return

        for i, o in enumerate(offset_table):
            if not o:
                yield data[:0]
                continue
            for p in offset_table[i+1:]:
                if p:
                    yield data[o:p]
                    break
            else:
                yield data[o:]

    def close(self):
        for c in self._chunks:
            c.release()
        self._sub_container.release()
        self._metadata.release()
        self._ldata.release()
        self._data.release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

class TMCParser(ContainerParser):
    def __init__(self, data, ldata):
        super().__init__(b'TMC', data)
        
        name,*_ = self._metadata[0x20:0x30].tobytes().partition(b'\0')
        self.metadata = TMCMetaData(name)

        o = 0xc0
        p = o+4*len(self._chunks)
        tbl = self._metadata[o:p].cast('I')
        i = indexOf(tbl, 0x8000_0020)
        self.lheader = LHeaderParser(self._chunks[i], ldata)

        for t, c in zip(tbl, self._chunks):
            match t:
                case 0x4:
                    self.collide = c
                case 0x5:
                    self.mtrlchng = c
                case 0x6:
                    self.effcnf = c
                case 0x9:
                    self.acscls = c
                case 0x45_50_4d_31:
                    self.epm1 = c
                case 0x8000_0001:
                    self.mdlgeo = c and MdlGeoParser(c, getattr(self.lheader, 'mdlgeo', b''))
                case 0x8000_0002:
                    self.ttdm = c and TTDMParser(c, getattr(self.lheader, 'ttdl', b''))
                case 0x8000_0003:
                    self.vtxlay = c and VtxLayParser(c, getattr(self.lheader, 'vtxlay', b''))
                case 0x8000_0004:
                    self.idxlay = c and IdxLayParser(c, getattr(self.lheader, 'idxlay', b''))
                case 0x8000_0005:
                    self.mtrcol = c and MtrColParser(c, getattr(self.lheader, 'mtrcol', b''))
                case 0x8000_0006:
                    self.mdlinfo = c and MdlInfoParser(c, getattr(self.lheader, 'mdlinfo', b''))
                case 0x8000_0010:
                    self.hielay = c and HieLayParser(c, getattr(self.lheader, 'hielay', b''))
                case 0x8000_0030:
                    self.nodelay = c and NodeLayParser(c, getattr(self.lheader, 'nodelay', b''))
                case 0x8000_0040:
                    self.glblmtx = c and GlblMtxParser(c, getattr(self.lheader, 'glblmtx', b''))
                case 0x8000_0050:
                    self.bnofsmtx = c and BnOfsMtxParser(c, getattr(self.lheader, 'bnofsmtx', b''))
                case 0x8000_0060:
                    self.cpf = c and cpfParser(c)
                case 0x8000_0070:
                    self.mcapack = c and MCAPACKParser(c)
                case 0x8000_0080:
                    self.renpack = c and RENPACKParser(c)

    def close(self):
        super().close()
        self.lheader.close()
        (x := getattr(self, 'mdlgeo', None)) and x.close()
        (x := getattr(self, 'ttdm', None)) and x.close()
        (x := getattr(self, 'vtxlay', None)) and x.close()
        (x := getattr(self, 'idxlay', None)) and x.close()
        (x := getattr(self, 'mtrcol', None)) and x.close()
        (x := getattr(self, 'mdlinfo', None)) and x.close()
        (x := getattr(self, 'hielay', None)) and x.close()
        (x := getattr(self, 'nodelay', None)) and x.close()
        (x := getattr(self, 'glblmtx', None)) and x.close()
        (x := getattr(self, 'bnofsmtx', None)) and x.close()

class TMCMetaData(NamedTuple):
    name: bytes

class MdlGeoParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'MdlGeo', data)
        self.chunks = tuple( ObjGeoParser(c) for c in self._chunks )

    def close(self):
        super().close()
        for c in self.chunks:
            c.close()

class ObjGeoParser(ContainerParser):
    def __init__(self, data):
        super().__init__(b'ObjGeo', data)
        _, obj_index = struct.unpack_from('< Ii', self._metadata)
        name,*_ = self._metadata[0x20:].tobytes().partition(b'\0')
        self.metadata = ObjGeoMetaData(obj_index, name)
        self.sub_container = GeoDeclParser(self._sub_container)
        self.chunks = tuple(ObjGeoParser._gen_chunks(self._chunks))

    @staticmethod
    def _gen_chunks(chunks):
        for c in chunks:
            # NGS2 has more members than DOA5LR (+16 bytes)
            chunk_idx, mtrcol_index, texture_info_count = struct.unpack_from(f'< ii4xI', c)
            texture_info_offset_table = struct.unpack_from(f'< {texture_info_count}I', c, 0x10)

            # 0x10:0x30 and 0x50:0x60 written by the application
            (
                    geodecl_index,
                    transparent1, _, transparent2, _,

                    _, _, _, _,
                    _, disable_backface_cull, first_index_index, index_count,
                    first_vertex_index, vertex_count, _, _,
                    _
            ) = struct.unpack_from('< I4x IIII 16x IIII IIII IIII I', c, 0x38)
            I = ( c[o:o+0x7c] for o in texture_info_offset_table )
            yield ObjGeoChunk(chunk_idx, mtrcol_index, geodecl_index,
                              transparent1, transparent2, bool(disable_backface_cull),
                              first_index_index, index_count,
                              first_vertex_index, vertex_count,
                              tuple(ObjGeoParser._gen_texture_info(I)))

    @staticmethod
    def _gen_texture_info(data):
        # NGS2 has more members than DOA5LR (+12 bytes)
        for d in data:
            info_index, usage, texture_buffer_index, _, tag1 = struct.unpack_from('< IIII I', d)
            yield TextureInfo(info_index, TextureUsage(usage), texture_buffer_index, tag1)

    def close(self):
        super().close()
        self.sub_container.close()

class ObjGeoMetaData(NamedTuple):
    #unknown0x0: int
    obj_index: int
    name: bytes
    
class ObjGeoChunk(NamedTuple):
    chunk_index: int
    mtrcol_index: int
    geodecl_chunk_index: int
    transparent1: int
    #unknown_0x44: int
    transparent2: int
    #unknown_0x4c: int

    #unknown_0x60: int
    #unknown_0x64: int
    #unknown_0x68: int
    #unknown_0x6c: int
    #unknown_0x70: int
    disable_backface_cull: bool
    first_index_index: int
    index_count: int
    first_vertex_index: int
    vertex_count: int
    #unknown_0x88: int
    #unknown_0x8c: int
    #unknown_0x90: int
    texture_info_table: tuple[TextureInfo]

class TextureInfo(NamedTuple):
    info_index: int
    usage: TextureUsage
    texture_buffer_index: int
    #unknown_0x10: int
    tag1: int
    #unknown_0x14: int
    #unknown_0x78: int

class TextureUsage(IntEnum):
    Albedo = 0
    Normal = 1
    Specular = 2
    Emission = 3

class GeoDeclParser(ContainerParser):
    def __init__(self, data):
        super().__init__(b'GeoDecl', data)
        self.chunks = tuple(GeoDeclParser._gen_chunks(self._chunks))

    @staticmethod
    def _gen_chunks(chunks):
        for c in chunks:
            (
                    _, vertex_info_offset, _, index_buffer_index,
                    index_count, vertex_count, _
            ) = struct.unpack_from('< IIII III', c)

            (
                    vertex_buffer_index, vertex_size, vertex_elements_count
            ) = struct.unpack_from('< III', c, vertex_info_offset)

            o = vertex_info_offset + 0x18
            E = ( c[i:i+8] for i in range(o, 8*vertex_elements_count+o, 8) )
            yield GeoDeclChunk(index_buffer_index, index_count,
                               vertex_count, vertex_buffer_index, vertex_size,
                               tuple(GeoDeclParser._gen_d3dvertexelement9(E)))

    @staticmethod
    def _gen_d3dvertexelement9(data):
        for d in data:
            (stream, offset, d3d_decl_type,
             method, usage, usage_index) = struct.unpack('< hhBBBB', d)
            yield D3DVERTEXELEMENT9(stream, offset, D3DDECLTYPE(d3d_decl_type),
                                    method, D3DDECLUSAGE(usage), usage_index)

class GeoDeclChunk(NamedTuple):
    #unknown0x0: int # always 0
    #unknown0x8: int # always 1
    index_buffer_index: int
    index_count: int
    vertex_count: int
    #unknown0x18: int # 0, 1 or 3
    # e.g. MOT* and OPTblur have 0, WGTmodel's decl1 has 1,
    # OPTscat and WGTmodel's decl0 have 3,
    vertex_buffer_index: int
    vertex_size: int
    vertex_elements: tuple[D3DVERTEXELEMENT9]

class D3DVERTEXELEMENT9(NamedTuple):
    stream: int
    offset: int
    d3d_decl_type: D3DDECLTYPE
    method: int
    usage: D3DDECLUSAGE
    usage_index: int

class D3DDECLTYPE(IntEnum):
    FLOAT1     = 0
    FLOAT2     = 1
    FLOAT3     = 2
    FLOAT4     = 3
    D3DCOLOR   = 4
    UBYTE4     = 5
    SHORT2     = 6
    SHORT4     = 7
    UBYTE4N    = 8
    SHORT2N    = 9
    SHORT4N    = 10
    USHORT2N   = 11
    USHORT4N   = 12
    UDEC3      = 13
    DEC3N      = 14
    FLOAT16_2  = 15
    FLOAT16_4  = 16
    UNUSED     = 17

class D3DDECLUSAGE(IntEnum):
    POSITION      = 0
    BLENDWEIGHT   = 1
    BLENDINDICES  = 2
    NORMAL        = 3
    PSIZE         = 4
    TEXCOORD      = 5
    TANGENT       = 6
    BINORMAL      = 7
    TESSFACTOR    = 8
    POSITIONT     = 9
    COLOR         = 10
    FOG           = 11
    DEPTH         = 12
    SAMPLE        = 13

class TTDMParser(ContainerParser):
    def __init__(self, data, ldata):
        super().__init__(b'TTDM', data)
        self.metadata = TTDHParser(self._metadata)
        self.sub_container = TTDLParser(self._sub_container, ldata)

    def close(self):
        super().close()
        self.metadata.close()
        self.sub_container.close()

class TTDHParser(ContainerParser):
    def __init__(self, data):
        super().__init__(b'TTDH', data)
        self.chunks = tuple(TTDHParser._gen_chunks(self._chunks))

    @staticmethod
    def _gen_chunks(chunks):
        for c in chunks:
            # If is_in_L is true, the index points to TTDL, otherwise it points to TTDM.
            # Although, all data seems be in TTDL when it comes to NGS2 TMC.
            is_in_L, index = struct.unpack_from('< Ii', c)
            yield TTDHChunk(bool(is_in_L), index)

class TTDHChunk(NamedTuple):
    is_in_L: bool
    chunk_index: int

class TTDLParser(ContainerParser):
    def __init__(self, data, ldata):
        super().__init__(b'TTDL', data, ldata)

class VtxLayParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'VtxLay', data, ldata)

class IdxLayParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'IdxLay', data, ldata)
        self.chunks = tuple( c.cast('H') for c in self._chunks )

    def close(self):
        super().close()
        for c in self.chunks:
            c.release()

class MtrColParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'MtrCol', data)
        self.chunks = tuple(MtrColParser._gen_chunks(self._chunks))

    @staticmethod
    def _gen_chunks(chunks):
        for c in chunks:
            *colors, mtrcol_idx, xrefs_count = struct.unpack_from('< 52f iI', c)
            xrefs = struct.unpack_from('<' + xrefs_count*'iI', c, 0xd8)
            xrefs = tuple(xrefs[i:i+2] for i in range(0, len(xrefs), 2))
            yield MtrColChunk(colors, mtrcol_idx, xrefs)

class MtrColChunk(NamedTuple):
    colors: tuple[float]
    chunk_index: int
    # Each tuple has (objindex, count)
    # that means the mtrcol is used by "objindex" "count" times
    xrefs: tuple[tuple[int, int]]

class MdlInfoParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'MdlInfo', data)
        self.chunks = tuple( ObjInfoParser(c) for c in self._chunks )

    def close(self):
        super().close()
        for c in self.chunks:
            c.close()

class ObjInfoParser(ContainerParser):
    def __init__(self, data):
        super().__init__(b'ObjInfo', data)
        _, obj_index, _ = struct.unpack_from('< Ii4xI', self._metadata)
        self.metadata = ObjInfoMetaData(obj_index)

class ObjInfoMetaData(NamedTuple):
    #unknown0x0: int # always 0x03000200
    obj_index: int
    #unknown0xc: int
    #unknown0x14: int

class HieLayParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'HieLay', data)
        self.chunks = tuple(HieLayParser._gen_chunks(self._chunks))

    @staticmethod
    def _gen_chunks(chunks):
        for c in chunks:
            *matrix, parent, children_count, level = struct.unpack_from('< 16f iII', c)
            children = struct.unpack_from(f'< {children_count}i', c, 0x50)
            yield HieLayChunk(matrix, parent, level, children)

class HieLayChunk(NamedTuple):
    matrix: tuple[float]
    parent: int
    level: int
    children: tuple[int]

class LHeaderParser(ContainerParser):
    def __init__(self, data, ldata):
        super().__init__(b'LHeader', data, ldata)

        o1 = 0x20
        o2 = 0x20 + 4*len(self._chunks)
        chunk_type_id_table = self._metadata[o1:o2].cast('I')
        for c, t in zip(self._chunks, chunk_type_id_table):
            match t:
                case 0xC000_0001:
                    self.mdlgeo = c
                case 0xC000_0002:
                    self.ttdl = c
                case 0xC000_0003:
                    self.vtxlay = c
                case 0xC000_0004:
                    self.idxlay = c
                case 0xC000_0005:
                    self.mtrcol = c
                case 0xC000_0006:
                    self.mdlinfo = c
                case 0xC000_0010:
                    self.hielay = c
                case 0xC000_0030:
                    self.nodelay = c
                case 0xC000_0040:
                    self.glblmtx = c
                case 0xC000_0050:
                    self.bnofsmtx = c
                case 0xC000_0060:
                    self.cpf = c
                case 0xC000_0070:
                    self.mcapack = c
                case 0xC000_0080:
                    self.renpack = c

class NodeLayParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'NodeLay', data)
        self.chunks = tuple( NodeObjParser(c) for c in self._chunks )

    def close(self):
        super().close()
        for c in self.chunks:
            c.close()

class NodeObjParser(ContainerParser):
    def __init__(self, data):
        super().__init__(b'NodeObj', data)
        _, master, node_index = struct.unpack_from('< Iii', self._metadata)
        name,*_ = self._metadata[0x10:].tobytes().partition(b'\0')
        self.metadata = NodeObjMetaData(master, node_index, name)
        if self._chunks:
            c = self._chunks[0]
            obj_index, node_count, node_index, *matrix = struct.unpack_from('< iIi4x 16f', c)
            node_group = struct.unpack_from(f'< {node_count}i', c, 0x50)
            self.chunks = (NodeObjChunk(obj_index, node_index, matrix, node_group),)

class NodeObjMetaData(NamedTuple):
    #unknown0x0: int
    master: int
    node_index: int
    name: bytes

class NodeObjChunk(NamedTuple):
    obj_index: int
    node_index: int
    matrix: tuple[float]
    node_group: tuple[int]
    
class GlblMtxParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'GlblMtx', data)
        self.chunks = tuple( struct.unpack_from('< 16f', c) for c in self._chunks )

class BnOfsMtxParser(ContainerParser):
    def __init__(self, data, ldata = b''):
        super().__init__(b'BnOfsMtx', data)
        self.chunks = tuple( struct.unpack_from('< 16f', c) for c in self._chunks )
