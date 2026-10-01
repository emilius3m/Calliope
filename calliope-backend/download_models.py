import os
import sys
import time
import requests
import urllib3

urllib3.disable_warnings()

DOWNLOADS = [
    # LoRAs and small models
    {
        "name": "Wan2_1_VAE_bf16.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\vae\Wan2_1_VAE_bf16.safetensors",
    },
    {
        "name": "Wan21_AccVid_T2V_14B_lora_rank32_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan21_AccVid_T2V_14B_lora_rank32_fp16.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\Wan21_AccVid_T2V_14B_lora_rank32_fp16.safetensors",
    },
    {
        "name": "Wan14B_RealismBoost.safetensors",
        "url": "https://huggingface.co/vrgamedevgirl84/Wan14BT2VFusioniX/resolve/main/OtherLoRa's/Wan14B_RealismBoost.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\Wan14B_RealismBoost.safetensors",
    },
    {
        "name": "DetailEnhancerV1.safetensors",
        "url": "https://huggingface.co/vrgamedevgirl84/Wan14BT2VFusioniX/resolve/main/OtherLoRa%27s/DetailEnhancerV1.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\WAN_DetailEnhancerV1.safetensors",
        "extra_copy": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\DetailEnhancerV1.safetensors",
    },
    {
        "name": "Wan21_T2V_14B_MoviiGen_lora_rank32_fp16.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan21_T2V_14B_MoviiGen_lora_rank32_fp16.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\Wan21_T2V_14B_MoviiGen_lora_rank32_fp16.safetensors",
    },
    {
        "name": "Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors",
    },
    {
        "name": "Wan2.1-Fun-14B-InP-MPS.safetensors",
        "url": "https://huggingface.co/alibaba-pai/Wan2.1-Fun-Reward-LoRAs/resolve/main/Wan2.1-Fun-14B-InP-MPS.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\loras\Wan2.1-Fun-14B-InP-MPS.safetensors",
    },
    {
        "name": "RealESRGAN_x4plus.pth",
        "url": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\upscale_models\RealESRGAN_x4plus.pth",
    },
    {
        "name": "RealESRGAN_x2plus.pth",
        "url": "https://huggingface.co/dtarnow/UPscaler/resolve/main/RealESRGAN_x2plus.pth",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\upscale_models\RealESRGAN_x2plus.pth",
    },
    {
        "name": "4x_foolhardy_Remacri.pth",
        "url": "https://huggingface.co/FacehugmanIII/4x_foolhardy_Remacri/resolve/main/4x_foolhardy_Remacri.pth",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\upscale_models\4x_foolhardy_Remacri.pth",
    },
    {
        "name": "rife49.pth",
        "url": "https://huggingface.co/MachineDelusions/RIFE/resolve/main/rife49.pth",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\frame_interpolation\rife49.pth",
        "extra_copy": r"E:\ComfyUI-Easy-Install\ComfyUI\custom_nodes\ComfyUI-Frame-Interpolation\ckpts\rife\rife49.pth",
    },
    # Main Wan 14B model & Text encoder
    {
        "name": "Wan2_1-T2V-14B_fp8_e4m3fn.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1-T2V-14B_fp8_e4m3fn.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\diffusion_models\Wan2_1-T2V-14B_fp8_e4m3fn.safetensors",
    },
    {
        "name": "umt5-xxl-enc-bf16.safetensors",
        "url": "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors",
        "dest": r"E:\ComfyUI-Easy-Install\ComfyUI\models\text_encoders\umt5-xxl-enc-bf16.safetensors",
    },
]

def download_file(item):
    name = item["name"]
    url = item["url"]
    dest = item["dest"]
    extra_copy = item.get("extra_copy")

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if extra_copy:
        os.makedirs(os.path.dirname(extra_copy), exist_ok=True)

    part_file = dest + ".part"

    # Check if already complete
    if os.path.exists(dest):
        # Verify size with HEAD request
        try:
            head_resp = requests.head(url, allow_redirects=True, timeout=15, verify=False)
            expected_size = int(head_resp.headers.get("content-length", 0))
            current_size = os.path.getsize(dest)
            if expected_size > 0 and current_size == expected_size:
                print(f"[SKIP] {name} already exists and size matches ({current_size / (1024*1024):.1f} MB)")
                if extra_copy and not os.path.exists(extra_copy):
                    import shutil
                    shutil.copy2(dest, extra_copy)
                    print(f"  [COPIED] to {extra_copy}")
                return
        except Exception as e:
            print(f"[WARN] Could not verify existing file size for {name}: {e}")

    # Resume support
    headers = {}
    downloaded_bytes = 0
    if os.path.exists(part_file):
        downloaded_bytes = os.path.getsize(part_file)
        headers["Range"] = f"bytes={downloaded_bytes}-"
        print(f"[RESUME] Resuming {name} from {downloaded_bytes / (1024*1024):.1f} MB...")
    else:
        print(f"[START] Starting download of {name}...")

    session = requests.Session()
    resp = session.get(url, headers=headers, stream=True, allow_redirects=True, timeout=30, verify=False)
    
    if resp.status_code == 416: # Range not satisfiable (file already finished in part)
        total_size = downloaded_bytes
    elif resp.status_code in [200, 206]:
        content_length = resp.headers.get("content-length")
        if content_length:
            total_size = int(content_length) + (downloaded_bytes if resp.status_code == 206 else 0)
        else:
            total_size = 0
    else:
        print(f"[FAIL] HTTP error {resp.status_code} for {name} ({url})")
        return

    mode = "ab" if resp.status_code == 206 else "wb"
    if resp.status_code == 200:
        downloaded_bytes = 0

    chunk_size = 1024 * 1024 * 4 # 4MB chunks
    start_time = time.time()
    last_print = start_time
    written_since_print = 0

    with open(part_file, mode) as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded_bytes += len(chunk)
                written_since_print += len(chunk)

                now = time.time()
                if now - last_print >= 5.0: # Print every 5 seconds
                    speed_mb = (written_since_print / (1024 * 1024)) / (now - last_print)
                    pct = (downloaded_bytes / total_size * 100) if total_size else 0
                    print(f"[{name}] {downloaded_bytes / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB ({pct:.1f}%) @ {speed_mb:.2f} MB/s", flush=True)
                    last_print = now
                    written_since_print = 0

    # Finished
    if os.path.exists(dest):
        os.remove(dest)
    os.rename(part_file, dest)
    print(f"[DONE] Successfully downloaded {name} ({os.path.getsize(dest) / (1024*1024):.1f} MB)", flush=True)

    if extra_copy:
        import shutil
        shutil.copy2(dest, extra_copy)
        print(f"  [COPIED] to {extra_copy}")

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"=== Model Downloader Starting (Target: {target}) ===")
    
    for item in DOWNLOADS:
        if target == "small" and any(k in item["name"] for k in ["Wan2_1-T2V-14B", "umt5-xxl"]):
            continue
        if target == "large" and not any(k in item["name"] for k in ["Wan2_1-T2V-14B", "umt5-xxl"]):
            continue
        if target not in ["all", "small", "large"] and target.lower() not in item["name"].lower():
            continue
        
        try:
            download_file(item)
        except Exception as e:
            print(f"[ERR] Failed to download {item['name']}: {e}", flush=True)

    print("=== Finished batch ===")
