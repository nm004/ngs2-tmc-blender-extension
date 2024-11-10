# NINJA GAIDEN SIGMA 2 TMC Importer by Nozomi Miyamori is under the public domain
# and also marked with CC0 1.0. This file is a part of NINJA GAIDEN SIGMA 2 TMC Importer.

from .parser import (
    TMCParser, TextureMapUsage, D3DDECLUSAGE, D3DDECLTYPE
)
import bpy
import bmesh
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix, Vector

from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, CollectionProperty
from bpy.types import Operator, OperatorFileListElement

import tempfile
import struct
import os
import os.path
import mmap

def import_tmc11(context, tmc):
    tmc_name = tmc.metadata.name.decode()

    # We load textures
    # TODO: Use delete_on_close=False instead of delete=False when Blender has begun to ship Python 3.12
    images = []
    n = len(tmc.ttdm.sub_container.chunks) // 10 + 1
    with tempfile.NamedTemporaryFile(delete=False) as t:
        for i,c in enumerate(tmc.ttdm.sub_container.chunks):
            t.close()
            with open(t.name, t.file.mode) as f:
                f.write(c)
            images.append(x := bpy.data.images.load(t.name))
            x.pack()
            x.name = str(tmc_name + f'_{i:0{n}}')
            x.filepath_raw = ''
    os.remove(t.name)

    # We add materials
    materials = []
    texmapinfo_to_matindex = {}
    format0 = len(tmc.mtrcol.chunks) // 10 + 1
    #uvnames = [ 'UVMap0', 'UVMap1', 'UVMap2', 'UVMap3' ]
    uvnames = [ '', 'UVMap1', 'UVMap2', 'UVMap3' ]
    for i, c in enumerate(tmc.mtrcol.chunks):
        S = set()
        for x in c.xrefs:
            for oc in tmc.mdlgeo.chunks[x[0]].chunks:
                if oc.mtrcol_index == i:
                    S.add(oc.texture_map_info)

        format1 = len(S) // 10 + 1
        for i, T in enumerate(S):
            m = bpy.data.materials.new(tmc_name + f'_{c.index:0{format0}}_{i:0{format1}}')
            m.preview_render_type = 'FLAT'
            m.use_nodes = True
            materials.append(m)
            # We assume that each texture corresponds to just a single material.
            # The material slot 0 is for fallback.
            texmapinfo_to_matindex[T] = len(materials)
            pbsdf = m.node_tree.nodes["Principled BSDF"]
            #pbsdf.inputs['Metallic'].default_value = .25
            pbsdf.inputs['IOR'].default_value = 1.0
            uv_idx = 0
            base_mixn = None
            for t in T:
                ti = m.node_tree.nodes.new('ShaderNodeTexImage')
                ti.image = images[t.texture_buffer_index]
                uvn = m.node_tree.nodes.new('ShaderNodeUVMap')
                uv_map = uvnames[uv_idx]
                uv_idx += 1
                uvn.uv_map = uv_map
                m.node_tree.links.new(uvn.outputs['UV'], ti.inputs['Vector'])
                match t.usage:
                    case TextureMapUsage.Albedo:
                        match t.version:
                            case 0:
                                ti.image.colorspace_settings.name = 'Non-Color'
                            case 3:
                                m.node_tree.links.new(ti.outputs['Color'], pbsdf.inputs['Base Color'])
                                m.node_tree.links.new(ti.outputs['Alpha'], pbsdf.inputs['Alpha'])
                            case 5:
                                if not base_mixn:
                                    base_mixn = m.node_tree.nodes.new('ShaderNodeMix')
                                    base_mixn.data_type = 'RGBA'
                                    base_mixn.blend_type = 'OVERLAY'
                                    m.node_tree.links.new(ti.outputs['Color'], base_mixn.inputs['A'])
                                    m.node_tree.links.new(base_mixn.outputs['Result'], pbsdf.inputs['Base Color'])
                                    m.node_tree.links.new(ti.outputs['Alpha'], pbsdf.inputs['Alpha'])
                                else:
                                    m.node_tree.links.new(ti.outputs['Color'], base_mixn.inputs['B'])
                                    m.node_tree.links.new(ti.outputs['Alpha'], base_mixn.inputs['Factor'])
                            case x:
                                raise ValueError('Not supported albedo texture format: {repr(x)}')
                    case TextureMapUsage.Normal:
                        ti.image.colorspace_settings.name = 'Non-Color'
                        nml = m.node_tree.nodes.new('ShaderNodeNormalMap')
                        nml.uv_map = uv_map
                        curv = m.node_tree.nodes.new('ShaderNodeRGBCurve')
                        curv_p = curv.mapping.curves[1].points
                        curv_p[0].location = (0, 1)
                        curv_p[1].location = (1, 0)
                        m.node_tree.links.new(ti.outputs['Color'], curv.inputs['Color'])
                        m.node_tree.links.new(curv.outputs['Color'], nml.inputs['Color'])
                        m.node_tree.links.new(nml.outputs['Normal'], pbsdf.inputs['Normal'])
                    case TextureMapUsage.Smoothness:
                        ti.image.colorspace_settings.name = 'Non-Color'
                        i = m.node_tree.nodes.new('ShaderNodeInvert')
                        m.node_tree.links.new(ti.outputs['Color'], i.inputs['Color'])
                        m.node_tree.links.new(i.outputs['Color'], pbsdf.inputs['Roughness'])
                    case TextureMapUsage.AlbedoOverride:
                        uvn.uv_map = ''
                        m.node_tree.links.new(ti.outputs['Color'], pbsdf.inputs['Base Color'])
                        m.node_tree.links.new(ti.outputs['Alpha'], pbsdf.inputs['Alpha'])
                    case x:
                        raise ValueError(f'Not supported texture map usage: {repr(x)}')

    # We form an armature
    a = bpy.data.armatures.new(tmc_name)
    c = a.collections.new('MOT')
    a.collections.new('SUP')
    a.collections.new('WGT')
    a.collections.new('OPT')
    a.collections.new('WPB')
    c.is_solo = True
    armature_obj = bpy.data.objects.new(a.name, a)
    armature_obj.matrix_world  = axis_conversion(from_forward='-Z', from_up='Y').to_4x4()
    context.view_layer.layer_collection.collection.objects.link(armature_obj)

    active_obj_saved = context.view_layer.objects.active
    context.view_layer.objects.active = armature_obj
    bpy.ops.object.mode_set(mode='EDIT')
    for c in tmc.nodelay.chunks:
        i = c.chunks[0].obj_idx
        b = armature_obj.data.edit_bones.new(c.metadata.name.decode())
        armature_obj.data.collections[b.name[:3]].assign(b)
        m = Matrix(tuple(tuple(r) for r in tmc.bnofsmtx.chunks[i]))
        m.transpose()
        m.invert()
        b.matrix = m
    for c in tmc.nodelay.chunks:
        EB = armature_obj.data.edit_bones
        i = c.chunks[0].obj_idx
        pi = tmc.hielay.chunks[i].parent
        b = EB[i]
        if pi > -1:
            b.parent = EB[pi]
        else:
            r = b
    r.tail = (0, .075, 0)
    set_bone_tail(r)
    bpy.ops.object.mode_set(mode='OBJECT')
    C = armature_obj.data.collections
    for c in C:
        if c and not len(c.bones):
            C.remove(c)
    context.view_layer.objects.active = active_obj_saved

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

    bm = bmesh.new(use_operators=True)
    bm.from_mesh(m)

    # We add vertices to the mesh and save vertex properties to set them later
    deform_layer = bm.verts.layers.deform.verify()
    vert_start_indices = []
    vert_start_idx_next = 0
    blend_weights = []
    blend_indices = []
    vertex_normals = []
    uv0 = []
    uv1 = []
    uv2 = []
    uv3 = []
    for ii, og in enumerate(tmc.mdlgeo.chunks):
        wgt = og.metadata.name[:3] == b'MOT'
        vert_start_indices.append([])
        gm = Matrix(tmc.glblmtx.chunks[og.metadata.index])
        for c in og.sub_container.chunks:
            blend_weights.extend(c.vertex_count * ((0, 0, 0, 0), ))
            blend_indices.extend(c.vertex_count * ((0, 0, 0, 0), ))
            uv0.extend(c.vertex_count * (Vector((0,0)), ))
            uv1.extend(c.vertex_count * (Vector((0,0)), ))
            uv2.extend(c.vertex_count * (Vector((0,0)), ))
            uv3.extend(c.vertex_count * (Vector((0,0)), ))
            vertex_normals.extend(c.vertex_count * (Vector((0,0,0)), ))
            idx = vert_start_idx_next
            vert_start_idx_next += c.vertex_count
            vert_start_indices[-1].append(idx)
            V = tmc.vtxlay.chunks[c.vertex_buffer_index]
            for e in c.vertex_elements:
                O = enumerate( ( i+e.offset for i in range(0, c.vertex_count*c.vertex_size, c.vertex_size) ), idx )
                match e.usage:
                    case D3DDECLUSAGE.POSITION:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.FLOAT3:
                                for _, o in O:
                                    v = bm.verts.new(Vector(V[o:o+12].cast('f')) @ gm)
                                    v[deform_layer][ii] = wgt
                            case x:
                                raise ValueError('Not supported vert decl type for position: {repr(x)}')
                    case D3DDECLUSAGE.BLENDWEIGHT:
                        match e.d3d_decl_type:
                            # The type is not actually UDEC3, but looks like UBYTE4.
                            case D3DDECLTYPE.UDEC3:
                                for i, o in O:
                                    blend_weights[i] = V[o:o+4]
                            case x:
                                raise ValueError('Not supported vert decl type for blendweight: {repr(x)}')
                    case D3DDECLUSAGE.BLENDINDICES:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.UBYTE4:
                                for i, o in O:
                                    blend_indices[i] = V[o:o+4]
                            case x:
                                raise ValueError('Not supported vert decl type for blendindices: {repr(x)}')
                    case D3DDECLUSAGE.NORMAL:
                        match e.d3d_decl_type:
                            case D3DDECLTYPE.FLOAT3:
                                for i, o in O:
                                    vertex_normals[i] = V[o:o+12].cast('f')
                            case x:
                                raise ValueError('Not supported vert decl type for normal: {repr(x)}')
                    case D3DDECLUSAGE.TEXCOORD:
                        match e.usage_index:
                            case 0:
                                # They are not "short", but actually "float16".
                                match e.d3d_decl_type:
                                    case D3DDECLTYPE.USHORT2N:
                                        for i, o in O:
                                            uv0[i] = struct.unpack('ee', V[o:o+4])
                                            uv1[i] = struct.unpack('ee', V[o+4:o+8])
                                    case D3DDECLTYPE.SHORT4N:
                                        for i, o in O:
                                            uv0[i] = struct.unpack('ee', V[o:o+4])
                                    case x:
                                        raise ValueError('Not supported vert decl type for texcoord: {repr(x)}')
                            case 1:
                                match e.d3d_decl_type:
                                    case D3DDECLTYPE.USHORT2N:
                                        for i, o in O:
                                            uv2[i] = struct.unpack('ee', V[o:o+4])
                                            uv3[i] = struct.unpack('ee', V[o+4:o+8])
                                    case D3DDECLTYPE.SHORT4N:
                                        for i, o in O:
                                            uv2[i] = struct.unpack('ee', V[o:o+4])
                                    case x:
                                        raise ValueError('Not supported vert decl type for texcoord: {repr(x)}')
                            case x:
                                raise ValueError('Not supported usage index for texcoord: {repr(x)}')
                    case D3DDECLUSAGE.TANGENT:
                        pass
                    case x:
                        raise ValueError('Not supported vert decl usage: {repr(x)}')

    vert_start_indices.append([vert_start_idx_next])
    bm.verts.index_update()
    bm.verts.ensure_lookup_table()

    # Let's assign vertices which has blend weight to vertex groups
    for noc in tmc.nodelay.chunks:
        try:
            cc = noc.chunks[0]
        except IndexError:
            continue
        i = cc.obj_idx
        o1 = vert_start_indices[i][0]
        o2 = vert_start_indices[i+1][0]
        I = blend_indices[o1:o2]
        W = blend_weights[o1:o2]
        for ii,ww,v in zip(I, W, range(o1, o2)):
            #assert sum(ww) == 0xFF
            for i, w in zip(ii, ww):
                if w > 0:
                    bm.verts[v][deform_layer][cc.node_group[i]] = w/0xff

    # We make faces
    for og, vi in zip(tmc.mdlgeo.chunks, vert_start_indices):
        for c in og.chunks:
            i = og.sub_container.chunks[c.geodecl_index].index_buffer_index
            o = c.first_index_index
            I = tuple( vi[c.geodecl_index]+j for j in tmc.idxlay.chunks[i][o:o+c.index_count])
            cw = True
            mtrcol_index = texmapinfo_to_matindex[c.texture_map_info]
            
            # Tris in the index buffer is described as clockwise, counter-clockwise, clockwise,
            # and so on.
            g, h = lambda x: x, reversed
            for i in range(len(I)-2):
                if I[i] != I[i+1] and I[i+1] != I[i+2] and I[i+2] != I[i]:
                    f = bm.faces.new( bm.verts[i] for i in g(I[i:i+3]) )
                    f.material_index = mtrcol_index
                g, h = h, g

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

    #bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0000001)
    bm.to_mesh(mesh_obj.data)
    mesh_obj.data.normals_split_custom_set_from_vertices(tuple(v.normal for v in bm.verts))

    return {'FINISHED'}

def set_bone_tail(b):
    for c in b.children:
        bc = c.collections[0]
        if bc.name == 'MOT':
            match sum( 1 for cc in c.children if cc.collections[0] == bc ):
                case 0:
                    c.tail = c.head + c.parent.matrix.to_3x3() @ Vector((0, .075, 0))
                case 1:
                    for cc in c.children:
                        if cc.collections[0] == bc:
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

    def execute(self, context):
        if len(self.files) < 2:
            self.report({'ERROR'}, 'Select both a TMC file and a TMCL file')
            return {'CANCELLED'}

        f0 = os.path.join(self.directory, self.files[0].name)
        f1 = os.path.join(self.directory, self.files[1].name)
        with (open(f0, 'rb') as f0, open(f1, 'rb') as f1):
            m_f0 = mmap.mmap(f0.fileno(), 0, access=mmap.ACCESS_READ)
            m_f1 = mmap.mmap(f1.fileno(), 0, access=mmap.ACCESS_READ)
            tmc = TMCParser(m_f0, m_f1) if m_f0[:4] == b'TMC\x00' else TMCParser(context, m_f1, m_f0)
            return import_tmc11(context, tmc)
