import json
import os
import sqlite3
import shutil

# 1. Load visual workflow
visual_path = r'e:\Calliope\example_ComfyUI_workflows\Wan21_Restore_Enhance_Upscale_visual.json'
with open(visual_path, 'r', encoding='utf-8') as f:
    wf = json.load(f)

# Update node titles for Calliope tagging
for n in wf['nodes']:
    nid = n['id']
    if nid == 43: # VHS_LoadVideo
        n['title'] = '(Input:video) Input Video'
    elif nid == 16: # WanVideoTextEncode
        n['title'] = '(Input:prompt) Text Prompt'
    elif nid == 51: # VHS_VideoCombine (Enhanced 1x)
        n['title'] = '(Output:video) Enhanced Video'
    elif nid == 359: # VHS_VideoCombine (Upscaled 48fps)
        n['title'] = '(Output:video) Restored & Upscaled Video'
    elif nid == 367: # VHS_VideoCombine (Compare)
        n['title'] = 'Compare Video (Side-by-Side)'

# Also ensure node 363 (ImageUpscaleWithModel) connects directly to node 28 (WanVideoDecode)
# so the workflow doesn't depend on manual disk folder loading
# In visual workflow: link 163 connects node 361 slot 0 -> node 363 slot 1 (image)
# We change link 163 source to node 28 slot 0
for link in wf['links']:
    if link[0] == 163:
        # [link_id, origin_id, origin_slot, target_id, target_slot, type]
        link[1] = 28 # origin_id = 28 (WanVideoDecode)
        link[2] = 0  # origin_slot = 0 (images)
        print("Updated link 163: Node 28 (images) -> Node 363 (image)")

# Also update node 361 outputs and node 28 outputs link lists
for n in wf['nodes']:
    if n['id'] == 361:
        for out in n.get('outputs', []):
            if out.get('links') and 163 in out['links']:
                out['links'].remove(163)
    elif n['id'] == 28:
        for out in n.get('outputs', []):
            if out.get('name') == 'images':
                if 163 not in out['links']:
                    out['links'].append(163)

# Save updated visual workflow
with open(visual_path, 'w', encoding='utf-8') as f:
    json.dump(wf, f, indent=2)
print("Updated visual workflow saved to:", visual_path)

# Copy to ComfyUI user workflows
comfy_user_wf_dir = r"E:\ComfyUI-Easy-Install\ComfyUI\user\default\workflows\Calliope_Visual"
os.makedirs(comfy_user_wf_dir, exist_ok=True)
shutil.copy2(visual_path, os.path.join(comfy_user_wf_dir, "Wan21_Restore_Enhance_Upscale.json"))
print("Copied visual workflow to ComfyUI canvas folder:", comfy_user_wf_dir)

# Copy to visual_workflows_ComfyUI
visual_wf_dir = r"e:\Calliope\visual_workflows_ComfyUI"
os.makedirs(visual_wf_dir, exist_ok=True)
shutil.copy2(visual_path, os.path.join(visual_wf_dir, "Wan21_Restore_Enhance_Upscale.json"))
print("Copied visual workflow to:", visual_wf_dir)

# 2. Build API Prompt Format
# Construct explicit api_prompt dict
api_prompt = {
    "11": {
        "class_type": "LoadWanVideoT5TextEncoder",
        "inputs": {
            "t5_model": "umt5-xxl-enc-bf16.safetensors",
            "precision": "bf16",
            "offload_device": "offload_device",
            "quantization": "disabled"
        },
        "_meta": {"title": "LoadWanVideoT5TextEncoder"}
    },
    "16": {
        "class_type": "WanVideoTextEncode",
        "inputs": {
            "positive_prompt": "",
            "negative_prompt": "",
            "force_offload": True,
            "use_disk_cache": False,
            "device": "gpu",
            "t5": ["11", 0],
            "model_to_offload": ["22", 0]
        },
        "_meta": {"title": "(Input:prompt) Text Prompt"}
    },
    "22": {
        "class_type": "WanVideoModelLoader",
        "inputs": {
            "model": "Wan2_1-T2V-14B_fp8_e4m3fn.safetensors",
            "base_precision": "fp16_fast",
            "quantization": "disabled",
            "load_device": "offload_device",
            "attention_mode": "sdpa",
            "rms_norm_function": "default",
            "lora": ["324", 0],
            "block_swap_args": ["55", 0]
        },
        "_meta": {"title": "WanVideoModelLoader"}
    },
    "27": {
        "class_type": "WanVideoSampler",
        "inputs": {
            "steps": 4,
            "cfg": 1.0,
            "shift": 1.0,
            "seed": 777533186908179,
            "force_offload": True,
            "scheduler": "flowmatch_causvid",
            "riflex_freq_index": 0,
            "denoise_strength": 0.5,
            "rope_function": "comfy",
            "start_step": 0,
            "end_step": -1,
            "add_noise_to_samples": False,
            "model": ["22", 0],
            "image_embeds": ["42", 0],
            "text_embeds": ["16", 0],
            "samples": ["37", 0],
            "context_options": ["346", 0]
        },
        "_meta": {"title": "WanVideoSampler"}
    },
    "28": {
        "class_type": "WanVideoDecode",
        "inputs": {
            "enable_vae_tiling": False,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
            "normalization": "default",
            "vae": ["38", 0],
            "samples": ["27", 0]
        },
        "_meta": {"title": "WanVideoDecode"}
    },
    "37": {
        "class_type": "WanVideoEmptyEmbeds",
        "inputs": {
            "width": 512,
            "height": 512,
            "num_frames": 53
        },
        "_meta": {"title": "WanVideoEmptyEmbeds"}
    },
    "38": {
        "class_type": "WanVideoVAELoader",
        "inputs": {
            "model_name": "Wan2_1_VAE_bf16.safetensors",
            "precision": "bf16",
            "use_cpu_cache": False,
            "verbose": False
        },
        "_meta": {"title": "WanVideoVAELoader"}
    },
    "42": {
        "class_type": "WanVideoEncode",
        "inputs": {
            "enable_vae_tiling": False,
            "tile_x": 272,
            "tile_y": 272,
            "tile_stride_x": 144,
            "tile_stride_y": 128,
            "noise_aug_strength": 0.0,
            "latent_strength": 1.0,
            "vae": ["38", 0],
            "image": ["54", 0]
        },
        "_meta": {"title": "WanVideoEncode"}
    },
    "43": {
        "class_type": "VHS_LoadVideo",
        "inputs": {
            "video": "input.mp4",
            "force_rate": 24,
            "custom_width": 0,
            "custom_height": 0,
            "frame_load_cap": 0,
            "skip_first_frames": 0,
            "select_every_nth": 1,
            "format": "AnimateDiff"
        },
        "_meta": {"title": "(Input:video) Input Video"}
    },
    "51": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "frame_rate": 24,
            "loop_count": 0,
            "filename_prefix": "VideoEnhance/Output",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 19,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
            "images": ["28", 0],
            "audio": ["376", 0]
        },
        "_meta": {"title": "(Output:video) Enhanced Video"}
    },
    "54": {
        "class_type": "ImageResizeKJv2",
        "inputs": {
            "width": 960,
            "height": 720,
            "upscale_method": "nearest-exact",
            "keep_proportion": "crop",
            "pad_color": "0, 0, 0",
            "crop_position": "center",
            "divisible_by": 2,
            "device": "cpu",
            "image": ["43", 0]
        },
        "_meta": {"title": "ImageResizeKJv2"}
    },
    "55": {
        "class_type": "WanVideoBlockSwap",
        "inputs": {
            "blocks_to_swap": 10,
            "offload_img_emb": True,
            "offload_txt_emb": True,
            "use_non_blocking": True,
            "vace_blocks_to_swap": 0,
            "prefetch_blocks": 0,
            "block_swap_debug": False
        },
        "_meta": {"title": "WanVideoBlockSwap"}
    },
    "318": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "Wan2.1-Fun-14B-InP-MPS.safetensors",
            "strength": 1.0,
            "low_mem_load": False,
            "merge_loras": True
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "319": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "Wan21_AccVid_T2V_14B_lora_rank32_fp16.safetensors",
            "strength": 1.0,
            "low_mem_load": False,
            "merge_loras": True,
            "prev_lora": ["318", 0]
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "320": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "Wan14B_RealismBoost.safetensors",
            "strength": 0.5,
            "low_mem_load": False,
            "merge_loras": True,
            "prev_lora": ["319", 0]
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "321": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "WAN_DetailEnhancerV1.safetensors",
            "strength": 0.5,
            "low_mem_load": False,
            "merge_loras": True,
            "prev_lora": ["320", 0]
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "323": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "Wan21_T2V_14B_MoviiGen_lora_rank32_fp16.safetensors",
            "strength": 1.0,
            "low_mem_load": False,
            "merge_loras": True,
            "prev_lora": ["321", 0]
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "324": {
        "class_type": "WanVideoLoraSelect",
        "inputs": {
            "lora_name": "Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
            "strength": 1.0,
            "low_mem_load": False,
            "merge_loras": True,
            "prev_lora": ["323", 0]
        },
        "_meta": {"title": "WanVideoLoraSelect"}
    },
    "346": {
        "class_type": "WanVideoContextOptions",
        "inputs": {
            "context_schedule": "uniform_standard",
            "context_frames": 81,
            "context_stride": 4,
            "context_overlap": 16,
            "freenoise": True,
            "verbose": False,
            "fuse_method": "linear"
        },
        "_meta": {"title": "WanVideoContextOptions"}
    },
    "359": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "frame_rate": 48,
            "loop_count": 0,
            "filename_prefix": "VideoEnhance/Upscale",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 8,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
            "images": ["386", 0],
            "audio": ["364", 0]
        },
        "_meta": {"title": "(Output:video) Restored & Upscaled Video"}
    },
    "362": {
        "class_type": "ImageScale",
        "inputs": {
            "upscale_method": "lanczos",
            "width": 2880,
            "height": 2160,
            "crop": "disabled",
            "image": ["363", 0]
        },
        "_meta": {"title": "ImageScale"}
    },
    "363": {
        "class_type": "ImageUpscaleWithModel",
        "inputs": {
            "upscale_model": ["365", 0],
            "image": ["28", 0]
        },
        "_meta": {"title": "ImageUpscaleWithModel"}
    },
    "364": {
        "class_type": "GetNode",
        "inputs": {},
        "_meta": {"title": "Get_Audio"}
    },
    "365": {
        "class_type": "UpscaleModelLoader",
        "inputs": {
            "model_name": "RealESRGAN_x4plus.pth"
        },
        "_meta": {"title": "UpscaleModelLoader"}
    },
    "366": {
        "class_type": "ImageConcatMulti",
        "inputs": {
            "inputcount": 2,
            "direction": "right",
            "match_image_size": False,
            "image_1": ["54", 0],
            "image_2": ["28", 0]
        },
        "_meta": {"title": "ImageConcatMulti"}
    },
    "367": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "frame_rate": 24,
            "loop_count": 0,
            "filename_prefix": "VideoEnhance/Compare",
            "format": "video/h264-mp4",
            "pix_fmt": "yuv420p",
            "crf": 8,
            "save_metadata": True,
            "trim_to_audio": False,
            "pingpong": False,
            "save_output": True,
            "images": ["366", 0]
        },
        "_meta": {"title": "Compare Video (Side-by-Side)"}
    },
    "375": {
        "class_type": "SetNode",
        "inputs": {
            "AUDIO": ["43", 2]
        },
        "_meta": {"title": "Set_Audio"}
    },
    "376": {
        "class_type": "GetNode",
        "inputs": {},
        "_meta": {"title": "Audio"}
    },
    "386": {
        "class_type": "RIFE_VFI",
        "inputs": {
            "ckpt_name": "rife49.pth",
            "clear_cache_after_n_frames": 10,
            "multiplier": 2,
            "fast_mode": True,
            "ensemble": True,
            "scale_factor": 1.0,
            "dtype": "float32",
            "torch_compile": False,
            "frames": ["362", 0]
        },
        "_meta": {"title": "RIFE VFI"}
    }
}

# Resolve canvas-only Set/Get nodes and use the installed nodes' API names.
api_prompt["11"]["inputs"]["model_name"] = api_prompt["11"]["inputs"].pop("t5_model")
api_prompt["11"]["inputs"]["load_device"] = api_prompt["11"]["inputs"].pop("offload_device")
api_prompt["27"]["inputs"]["image_embeds"] = ["37", 0]
api_prompt["27"]["inputs"]["samples"] = ["42", 0]
api_prompt["37"]["inputs"].update(width=["54", 1], height=["54", 2], num_frames=["43", 1])
for node_id in ("318", "319", "320", "321", "323", "324"):
    api_prompt[node_id]["inputs"]["lora"] = api_prompt[node_id]["inputs"].pop("lora_name")
for node_id in ("51", "359"):
    api_prompt[node_id]["inputs"]["audio"] = ["43", 2]
for node_id in ("364", "375", "376"):
    api_prompt.pop(node_id)
api_prompt["386"]["class_type"] = "RIFE VFI"
api_prompt["386"]["inputs"]["batch_size"] = 1

api_path = r'e:\Calliope\example_ComfyUI_workflows\Wan21_Restore_Enhance_Upscale_API.json'
with open(api_path, 'w', encoding='utf-8') as f:
    json.dump(api_prompt, f, indent=2)
print("Saved API workflow to:", api_path)

# 3. Test Calliope parsing on this API workflow
from calliope.comfyui.parser import parse_dynamic_inputs, parse_dynamic_outputs

inputs = parse_dynamic_inputs(api_prompt)
outputs = parse_dynamic_outputs(api_prompt)

print(f"\n--- Calliope Dynamic Inputs ({len(inputs)}) ---")
for inp in inputs:
    print(f"  Node #{inp['nodeId']}: role={inp['role']}, kind={inp['kind']}, label='{inp['label']}', default={repr(inp['defaultValue'])}")

print(f"\n--- Calliope Dynamic Outputs ({len(outputs)}) ---")
for out in outputs:
    print(f"  Node #{out['nodeId']}: role={out['role']}, kind={out['kind']}, label='{out['label']}'")

# 4. Register or Update in Calliope DB
conn = sqlite3.connect(r'e:\Calliope\calliope-backend\data\calliope.db')
name = "Wan21_Restore_Enhance_Upscale_API"
existing = conn.execute("SELECT id FROM workflows WHERE name = ?", (name,)).fetchone()

if existing:
    conn.execute(
        "UPDATE workflows SET workflow_json = ?, input_schema = ?, output_schema = ?, kind = 'video', is_enabled = 1 WHERE id = ?",
        (json.dumps(api_prompt), json.dumps(inputs), json.dumps(outputs), existing[0])
    )
    print(f"\n[DB] Updated existing workflow '{name}' (ID: {existing[0]})")
else:
    cur = conn.execute(
        "INSERT INTO workflows (name, kind, is_enabled, workflow_json, input_schema, output_schema) VALUES (?, 'video', 1, ?, ?, ?)",
        (name, json.dumps(api_prompt), json.dumps(inputs), json.dumps(outputs))
    )
    print(f"\n[DB] Registered new workflow '{name}' (ID: {cur.lastrowid})")

conn.commit()
conn.close()
print("Workflow registration complete!")
