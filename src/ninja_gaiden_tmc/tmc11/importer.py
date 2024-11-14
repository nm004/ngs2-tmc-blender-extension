# NINJA GAIDEN SIGMA 2 TMC Importer by Nozomi Miyamori is under the public domain
# and also marked with CC0 1.0. This file is a part of NINJA GAIDEN SIGMA 2 TMC Importer.

from .parser import (
    TMCParser, TextureUsage, D3DDECLUSAGE, D3DDECLTYPE
)
import bpy
import bmesh
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix, Vector

from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement

from itertools import accumulate
import tempfile
import struct
import os
import os.path
import mmap

def import_tmc11(context, tmc):
    tmc_name = tmc.metadata.name.decode()

    # We form an armature
    a = bpy.data.armatures.new(tmc_name)
    armature_obj = bpy.data.objects.new(a.name, a)
    armature_obj.matrix_world  = axis_conversion(from_forward='-Z', from_up='Y').to_4x4()
    context.view_layer.layer_collection.collection.objects.link(armature_obj)

    active_obj_saved = context.view_layer.objects.active
    context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')
    for c in tmc.nodelay.chunks:
        gm = tmc.glblmtx.chunks[c.metadata.node_index]
        b = a.edit_bones.new(c.metadata.name.decode())
        b.matrix = (gm[0:4], gm[4:8], gm[8:12], gm[12:16])
    for c in tmc.nodelay.chunks:
        i = c.metadata.node_index
        pi = tmc.hielay.chunks[i].parent
        if pi > -1:
            a.edit_bones[i].parent = a.edit_bones[pi]
        else:
            r = a.edit_bones[i]
    r.tail = (0, .075, 0)
    set_bone_tail(r)
    bpy.ops.object.mode_set(mode='OBJECT')
    context.view_layer.objects.active = active_obj_saved

    a = armature_obj.data
    a.collections.new('MOT').is_solo = True
    a.collections.new('SUP')
    a.collections.new('WGT')
    a.collections.new('OPT')
    a.collections.new('WPB')
    for b in a.bones:
        a.collections[b.name[:3]].assign(b)
    for c in a.collections.values():
        if not len(c.bones):
            a.collections.remove(c)

    # We load textures
    # TODO: Use delete_on_close=False instead of delete=False when Blender has begun to ship Python 3.12
    images = []
    n = len(str(len(tmc.ttdm.sub_container.chunks)))
    with tempfile.NamedTemporaryFile(delete=False) as t:
        for i,c in enumerate(tmc.ttdm.sub_container.chunks):
            t.close()
            with open(t.name, t.file.mode) as f:
                f.write(c)
            images.append(x := bpy.data.images.load(t.name))
            x.pack()
            x.name = str(tmc_name + '_' + str(i).zfill(n))
            x.filepath_raw = ''
    os.remove(t.name)

    # We make node gourps which represents MTRCOLs
    shader_node_groups = []
    #uvnames = [ 'UVMap0', 'UVMap1', 'UVMap2', 'UVMap3' ]
    uvnames = [ '', 'UVMap1', 'UVMap2', 'UVMap3' ]
    n0 = len(str(len(tmc.mtrcol.chunks)))
    for i, c in enumerate(tmc.mtrcol.chunks):
        ng = bpy.data.node_groups.new(tmc_name + '_' + str(i).zfill(n0), 'ShaderNodeTree')
        shader_node_groups.append(ng)
        ng.interface.new_socket('Factor', socket_type='NodeSocketFloat')
        ng.interface.new_socket('A', socket_type='NodeSocketColor')
        ng.interface.new_socket('B', socket_type='NodeSocketColor')
        ng.interface.new_socket('Alpha', socket_type='NodeSocketFloat')
        ng.interface.new_socket('Normal', socket_type='NodeSocketVector')
        ng.interface.new_socket('Specular Tint', socket_type='NodeSocketColor')
        ng.interface.new_socket('Emission Color', socket_type='NodeSocketColor')
        ng.interface.new_socket('Emission Strength', socket_type='NodeSocketFloat')
        ng.interface.new_socket('BSDF', in_out='OUTPUT', socket_type='NodeSocketShader')
        pbsdf = ng.nodes.new('ShaderNodeBsdfPrincipled')
        base_mix = ng.nodes.new('ShaderNodeMix')
        base_mix.data_type = 'RGBA'
        base_mix.blend_type = 'OVERLAY'
        ng.links.new(base_mix.outputs['Result'], pbsdf.inputs['Base Color'])
        I = ng.nodes.new('NodeGroupInput')
        ng.links.new(I.outputs['Factor'], base_mix.inputs['Factor'])
        ng.links.new(I.outputs['A'], base_mix.inputs['A'])
        ng.links.new(I.outputs['B'], base_mix.inputs['B'])
        ng.links.new(I.outputs['Alpha'], pbsdf.inputs['Alpha'])
        ng.links.new(I.outputs['Normal'], pbsdf.inputs['Normal'])
        ng.links.new(I.outputs['Specular Tint'], pbsdf.inputs['Specular Tint'])
        ng.links.new(I.outputs['Emission Color'], pbsdf.inputs['Emission Color'])
        ng.links.new(I.outputs['Emission Strength'], pbsdf.inputs['Emission Strength'])
        O = ng.nodes.new('NodeGroupOutput')
        ng.links.new(pbsdf.outputs['BSDF'], O.inputs['BSDF'])

    # Then, we add materials for each OBJGEO chunk
    materials = []
    # Material Slot 0 is for fallback
    matindex_next = 1
    objchunk_to_matindex = len(tmc.mdlgeo.chunks) * [ None ]
    # We use NODEOBJs because names in OBJGEO were omitted, although NODEOBJ has a full name.
    for c in tmc.nodelay.chunks:
        try:
            objgeo = tmc.mdlgeo.chunks[c.chunks[0].obj_index]
        except IndexError:
            continue
        name_pre = tmc_name + '_' + c.metadata.name.decode() + '_'
        s = matindex_next
        matindex_next += len(objgeo.chunks)
        objchunk_to_matindex[objgeo.metadata.obj_index] = range(s, matindex_next)
        n = len(str(len(objgeo.chunks)))
        for c in objgeo.chunks:
            m = bpy.data.materials.new(name_pre + str(c.chunk_index).zfill(n))
            materials.append(m)
            m.preview_render_type = 'FLAT'
            m.use_nodes = True
            m.node_tree.nodes.remove(m.node_tree.nodes["Principled BSDF"])
            ng = m.node_tree.nodes.new('ShaderNodeGroup')
            ng.node_tree = shader_node_groups[c.mtrcol_index]
            m.node_tree.links.new(ng.outputs['BSDF'], m.node_tree.nodes[0].inputs[0])
            uv_idx = 0
            for t in c.texture_info_table:
                frame = m.node_tree.nodes.new('NodeFrame')
                ti = m.node_tree.nodes.new('ShaderNodeTexImage')
                ti.image = images[t.texture_buffer_index]
                ti.parent = frame
                uv = m.node_tree.nodes.new('ShaderNodeUVMap')
                uv_map = uvnames[uv_idx]
                uv_idx += 1
                uv.uv_map = uv_map
                uv.parent = frame
                m.node_tree.links.new(uv.outputs['UV'], ti.inputs['Vector'])
                match t.usage:
                    case TextureUsage.Albedo:
                        frame.label = 'Albedo'
                        if ng.inputs['A'].is_linked:
                            m.node_tree.links.new(ti.outputs['Color'], ng.inputs['B'])
                            m.node_tree.links.new(ti.outputs['Alpha'], ng.inputs['Factor'])
                        else:
                            m.node_tree.links.new(ti.outputs['Color'], ng.inputs['A'])
                            m.node_tree.links.new(ti.outputs['Alpha'], ng.inputs['Alpha'])
                    case TextureUsage.Normal:
                        frame.label = 'Normal'
                        ti.image.colorspace_settings.name = 'Non-Color'
                        nml = m.node_tree.nodes.new('ShaderNodeNormalMap')
                        nml.uv_map = uv_map
                        nml.parent = frame
                        curv = m.node_tree.nodes.new('ShaderNodeRGBCurve')
                        curv_p = curv.mapping.curves[1].points
                        curv_p[0].location = (0, 1)
                        curv_p[1].location = (1, 0)
                        curv.parent = frame
                        m.node_tree.links.new(ti.outputs['Color'], curv.inputs['Color'])
                        m.node_tree.links.new(curv.outputs['Color'], nml.inputs['Color'])
                        m.node_tree.links.new(nml.outputs['Normal'], ng.inputs['Normal'])
                    case TextureUsage.Specular:
                        frame.label = 'Specular'
                        m.node_tree.links.new(ti.outputs['Color'], ng.inputs['Specular Tint'])
                    case TextureUsage.Emission:
                        frame.label = 'Emission'
                        uv.uv_map = ''
                        m.node_tree.links.new(ti.outputs['Color'], ng.inputs['Emission Color'])
                        m.node_tree.links.new(ti.outputs['Alpha'], ng.inputs['Emission Strength'])
                    case x:
                        raise ValueError(f'Not supported texture map usage: {repr(x)}')

    # We form a mesh below
    m = bpy.data.meshes.new(tmc_name)
    m.materials.append(None)
    for ma in materials:
        m.materials.append(ma)
    mesh_obj = bpy.data.objects.new(m.name, m)
    mesh_obj.parent = armature_obj
    for noc in tmc.nodelay.chunks:
        mesh_obj.vertex_groups.new(name=noc.metadata.name.decode())
    context.view_layer.layer_collection.collection.objects.link(mesh_obj)
    md = mesh_obj.modifiers.new('', 'ARMATURE')
    md.object = armature_obj

    bm = bmesh.new(use_operators=False)
    bm.from_mesh(m)

    # We add vertices to the mesh and de-interleave vertex properties to set them later
    deform_layer = bm.verts.layers.deform.verify()
    blend_weights = []
    blend_indices = []
    vertex_normals = []
    uv0 = []
    uv1 = []
    uv2 = []
    uv3 = []
    for c in tmc.nodelay.chunks:
        try:
            objgeo = tmc.mdlgeo.chunks[c.chunks[0].obj_index]
        except IndexError:
            continue
        nidx = c.metadata.node_index
        wgt = c.metadata.name.startswith(b'MOT') or c.metadata.name.startswith(b'OPT') or c.metadata.name.startswith(b'WPB')
        gm = tmc.glblmtx.chunks[nidx]
        gm = Matrix((gm[0:4], gm[4:8], gm[8:12], gm[12:16]))
        for declc_idx, c in enumerate(objgeo.sub_container.chunks):
            blend_weights.extend(c.vertex_count * ((0, 0, 0, 0), ))
            blend_indices.extend(c.vertex_count * ((0, 0, 0, 0), ))
            uv0.extend(c.vertex_count * (Vector((0,0)), ))
            uv1.extend(c.vertex_count * (Vector((0,0)), ))
            uv2.extend(c.vertex_count * (Vector((0,0)), ))
            uv3.extend(c.vertex_count * (Vector((0,0)), ))
            vertex_normals.extend(c.vertex_count * (Vector((0,0,0)), ))
            V = tmc.vtxlay.chunks[c.vertex_buffer_index]
            I = tmc.idxlay.chunks[c.index_buffer_index]
            R = range(0, c.vertex_count*c.vertex_size, c.vertex_size)
            n = len(bm.verts)
            for e in c.vertex_elements:
                VO = enumerate(range(e.offset, R.stop, R.step), n)
                match e.usage:
                    case D3DDECLUSAGE.POSITION:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.FLOAT3:
                                for _, o in VO:
                                    v = bm.verts.new(Vector(V[o:o+12].cast('f')) @ gm)
                                    v[deform_layer][nidx] = wgt
                                bm.verts.ensure_lookup_table()
                                for c in objgeo.chunks:
                                    material_index = objchunk_to_matindex[objgeo.metadata.obj_index][c.chunk_index]
                                    if c.geodecl_chunk_index == declc_idx:
                                        g, h = lambda x: x, reversed
                                        for i in range(c.first_index_index, c.first_index_index + c.index_count - 2):
                                            if I[i] != I[i+1] and I[i+1] != I[i+2] and I[i+2] != I[i]:
                                                try:
                                                    f = bm.faces.new( bm.verts[i + n] for i in g(I[i:i+3]) )
                                                    f.material_index = material_index
                                                except ValueError:
                                                    pass
                                            g, h = h, g
                            case x:
                                raise ValueError(f'Not supported vert decl type for position: {repr(x)}')
                    case D3DDECLUSAGE.BLENDWEIGHT:
                        match e.d3d_decl_type:
                            # The type is not actually UDEC3, but UBYTE4.
                            case D3DDECLTYPE.UDEC3:
                                for i, o in VO:
                                    blend_weights[i] = V[o:o+4]
                            case x:
                                raise ValueError(f'Not supported vert decl type for blendweight: {repr(x)}')
                    case D3DDECLUSAGE.BLENDINDICES:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.UBYTE4:
                                for i, o in VO:
                                    blend_indices[i] = V[o:o+4]
                            case x:
                                raise ValueError(f'Not supported vert decl type for blendindices: {repr(x)}')
                    case D3DDECLUSAGE.NORMAL:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.FLOAT3:
                                for i, o in VO:
                                    vertex_normals[i] = V[o:o+12].cast('f')
                            case x:
                                raise ValueError(f'Not supported vert decl type for normal: {repr(x)}')
                    case D3DDECLUSAGE.TEXCOORD:
                        match e.usage_index:
                            case 0:
                                # They are not "short", but actually "float16".
                                match e.d3d_decl_type:
                                    case D3DDECLTYPE.USHORT2N:
                                        for i, o in VO:
                                            uv0[i] = struct.unpack('ee', V[o:o+4])
                                            uv1[i] = struct.unpack('ee', V[o+4:o+8])
                                    case D3DDECLTYPE.SHORT4N:
                                        for i, o in VO:
                                            uv0[i] = struct.unpack('ee', V[o:o+4])
                                    case x:
                                        raise ValueError(f'Not supported vert decl type for texcoord: {repr(x)}')
                            case 1:
                                match e.d3d_decl_type:
                                    case D3DDECLTYPE.USHORT2N:
                                        for i, o in VO:
                                            uv2[i] = struct.unpack('ee', V[o:o+4])
                                            uv3[i] = struct.unpack('ee', V[o+4:o+8])
                                    case D3DDECLTYPE.SHORT4N:
                                        for i, o in VO:
                                            uv2[i] = struct.unpack('ee', V[o:o+4])
                                    case x:
                                        raise ValueError(f'Not supported vert decl type for texcoord: {repr(x)}')
                            case x:
                                raise ValueError(f'Not supported usage index for texcoord: {repr(x)}')
                    case D3DDECLUSAGE.TANGENT:
                        pass
                    case x:
                        raise ValueError(f'Not supported vert decl usage: {repr(x)}')

    bm.verts.index_update()

    # Let's assign vertices which has blend weight to corresponding vertex groups
    for v in bm.verts:
        vd = v[deform_layer]
        ng = tmc.nodelay.chunks[vd.keys()[0]].chunks[0].node_group
        for i, w in zip(blend_indices[v.index], blend_weights[v.index]):
            if w > 0:
                vd[ng[i]] = w/0xff

    # We add UVs
    l0 = bm.loops.layers.uv.new('UVMap0')
    l1 = bm.loops.layers.uv.new('UVMap1')
    l2 = bm.loops.layers.uv.new('UVMap2')
    l3 = bm.loops.layers.uv.new('UVMap3')
    for f in bm.faces:
        for lo in f.loops:
            uv = uv0[lo.vert.index]
            lo[l0].uv = (uv[0], 1-uv[1])
            uv = uv1[lo.vert.index]
            lo[l1].uv = (uv[0], 1-uv[1])
            uv = uv2[lo.vert.index]
            lo[l2].uv = (uv[0], 1-uv[1])
            uv = uv3[lo.vert.index]
            lo[l3].uv = (uv[0], 1-uv[1])

    # Custom normals still have to be set by calling normals_split_custom_set{_from_vertices}.
    for v in bm.verts:
        v.normal = vertex_normals[v.index]        

    bm.to_mesh(mesh_obj.data)
    mesh_obj.data.normals_split_custom_set_from_vertices(tuple(v.normal for v in bm.verts))

    return {'FINISHED'}

def set_bone_tail(b):
    for c in b.children:
        if c.name.startswith('MOT'):
            match sum( 1 for cc in c.children if cc.name.startswith('MOT')):
                case 0:
                    c.tail = c.head + c.parent.matrix.to_3x3() @ Vector((0, .075, 0))
                case 1:
                    for cc in c.children:
                        if cc.name.startswith('MOT'):
                            c.tail = c.head + .075 * (cc.head - c.head).normalized()
                            break
                case x:
                    c.tail = c.head + c.parent.matrix.to_3x3() @ Vector((0, .075, 0))
            set_bone_tail(c)
        else:
            c.tail = c.head + c.parent.matrix.to_3x3() @ Vector((0, .075, 0))

class ImportTMC11(Operator, ImportHelper):
    '''Load a TMC file and a TMCL file (NGS2)'''
    bl_idname = 'ninja_gaiden_tmc.import_tmc11'
    bl_label = 'Import TMC/TMCL'

    filter_glob: StringProperty(
        default="*.tmc;*.tmcl;*.dat",
        options={'SKIP_SAVE', 'HIDDEN'},
    )
    directory: bpy.props.StringProperty(
        subtype='FILE_PATH',
        options={'SKIP_SAVE', 'HIDDEN'}
    )
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={'SKIP_SAVE', 'HIDDEN'}
    )

    def _mmap_file(self, n):
        with open(os.path.join(self.directory, self.files[n].name), 'rb') as f:
            return mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

    def execute(self, context):
        if len(self.files) != 2:
            self.report({'ERROR'}, 'Select both a TMC file and a TMCL file')
            return {'CANCELLED'}

        m0, m1 = self._mmap_file(0), self._mmap_file(1)
        tmc = TMCParser(m0, m1) if m0[:8].startswith(b'TMC\0\0\0\0\0') else TMCParser(m1, m0)
        with m0, m1, tmc:
            return import_tmc11(context, tmc)
