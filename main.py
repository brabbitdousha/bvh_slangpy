import os
os.environ['CUDA_VISIBLE_DEVICES']='0'
import torch
import trimesh
import pyexr
import slangpy
import time
import csv
import numpy as np


m_gen_ele = slangpy.loadModule('bvhworkers/get_elements.slang')
m_morton_codes = slangpy.loadModule('bvhworkers/lbvh_morton_codes.slang')
m_radixsort = slangpy.loadModule('bvhworkers/lbvh_single_radixsort.slang')
m_hierarchy = slangpy.loadModule('bvhworkers/lbvh_hierarchy.slang')
m_bounding_box = slangpy.loadModule('bvhworkers/lbvh_bounding_boxes.slang')

#debug
#'''
input = torch.tensor((0.6,0.7,0.8), dtype=torch.float).cuda()
output = torch.zeros(input.shape, dtype=torch.int).cuda()
m_gen_ele.debug_cb(a=input, b=output)\
.launchRaw(blockSize=(1, 1, 1), gridSize=(1, 1, 1))
print(output)
#'''

mesh = trimesh.load('./models/dragon.obj')

vrt = torch.from_numpy(mesh.vertices).cuda().float()
v_ind = torch.from_numpy(mesh.faces).cuda().int()

start_time = time.time()
#first part, get element and bbox---------------
primitive_num = v_ind.shape[0]
ele_primitiveIdx = torch.zeros((primitive_num, 1), dtype=torch.int).cuda()
ele_aabb = torch.zeros((primitive_num, 6), dtype=torch.float).cuda()

# Invoke normally
m_gen_ele.generateElements(vert=vrt, v_indx=v_ind, ele_primitiveIdx=ele_primitiveIdx, ele_aabb=ele_aabb)\
    .launchRaw(blockSize=(256, 1, 1), gridSize=((primitive_num+255)//256, 1, 1))
extent_min_x = ele_aabb[:,0].min()
extent_min_y = ele_aabb[:,1].min()
extent_min_z = ele_aabb[:,2].min()

extent_max_x = ele_aabb[:,3].max()
extent_max_y = ele_aabb[:,4].max()
extent_max_z = ele_aabb[:,5].max()
num_ELEMENTS = ele_aabb.shape[0]
#-------------------------------------------------
#morton codes part
pcMortonCodes = m_morton_codes.pushConstantsMortonCodes(
    g_num_elements=num_ELEMENTS, g_min_x=extent_min_x, g_min_y=extent_min_y, g_min_z=extent_min_z,
    g_max_x=extent_max_x, g_max_y=extent_max_y, g_max_z=extent_max_z
)
morton_codes_ele = torch.zeros((num_ELEMENTS, 2), dtype=torch.int).cuda()

m_morton_codes.morton_codes(pc=pcMortonCodes, ele_aabb=ele_aabb, morton_codes_ele=morton_codes_ele)\
.launchRaw(blockSize=(256, 1, 1), gridSize=((num_ELEMENTS+255)//256, 1, 1))

#--------------------------------------------------
# radix sort part
morton_codes_ele_pingpong = torch.zeros((num_ELEMENTS, 2), dtype=torch.int).cuda()
m_radixsort.radix_sort(g_num_elements=int(num_ELEMENTS), g_elements_in=morton_codes_ele, g_elements_out=morton_codes_ele_pingpong)\
.launchRaw(blockSize=(256, 1, 1), gridSize=(1, 1, 1))

#--------------------------------------------------
# hierarchy
num_LBVH_ELEMENTS = num_ELEMENTS + num_ELEMENTS - 1
LBVHNode_info = torch.zeros((num_LBVH_ELEMENTS, 3), dtype=torch.int).cuda()
LBVHNode_aabb = torch.zeros((num_LBVH_ELEMENTS, 6), dtype=torch.float).cuda()
LBVHConstructionInfo = torch.zeros((num_LBVH_ELEMENTS, 2), dtype=torch.int).cuda()

m_hierarchy.hierarchy(g_num_elements=int(num_ELEMENTS), ele_primitiveIdx=ele_primitiveIdx, ele_aabb=ele_aabb,
                      g_sorted_morton_codes=morton_codes_ele, g_lbvh_info=LBVHNode_info, g_lbvh_aabb=LBVHNode_aabb, g_lbvh_construction_infos=LBVHConstructionInfo)\
.launchRaw(blockSize=(256, 1, 1), gridSize=((num_ELEMENTS+255)//256, 1, 1))

#--------------------------------------------------
# bounding_boxes

m_bounding_box.bounding_boxes(g_num_elements=int(num_ELEMENTS), g_lbvh_info=LBVHNode_info, g_lbvh_aabb=LBVHNode_aabb, g_lbvh_construction_infos=LBVHConstructionInfo)\
.launchRaw(blockSize=(256, 1, 1), gridSize=((num_ELEMENTS+255)//256, 1, 1))

end_time = time.time()
elapsed_time = end_time - start_time
print(f"GPU bvh build finished in: {elapsed_time} s")

#write
LBVHbuffer = np.concatenate((LBVHNode_info.cpu().numpy(), LBVHNode_aabb.cpu().numpy()), axis=-1)

with open('./data.csv', 'w') as csvfile:
    csvfile.write("left right primitiveIdx aabb_min_x aabb_min_y aabb_min_z aabb_max_x aabb_max_y aabb_max_z\n")
    np.savetxt(csvfile, LBVHbuffer, delimiter=' ', fmt='%g')

#debug
sorted_mc_codes = morton_codes_ele.cpu().numpy()
with open('./sorted_mc.txt', 'w') as mc:
    np.set_printoptions(suppress=True)
    np.savetxt(mc, sorted_mc_codes, delimiter=' ', fmt='%d')

print("over!")