# -*- coding: utf-8 -*-
"""IndexTTS 2 子进程 worker —— 在 index-tts/.venv 环境中运行

由 modules/tts_engine.py 的 IndexTTSEngine 调用：
  python index_tts_worker.py <job.json>

job.json 格式：
  {
    "model_dir": ".../index-tts/checkpoints",
    "use_fp16": true, "use_deepspeed": false, "use_accel": false,
    "items": [{"index": 0, "text": "...", "ref_audio": "...wav",
               "emo_audio": "...wav", "output_path": "...wav"}],
    "result_path": ".../_index_tts_result.json"
  }

模型只加载一次，逐条合成，结果写入 result_path。
"""

import json
import os
import sys
import traceback


def main():
    if len(sys.argv) < 2:
        print("用法: python index_tts_worker.py <job.json>")
        sys.exit(2)

    job_path = sys.argv[1]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)

    model_dir = job["model_dir"]
    cfg_path = os.path.join(model_dir, "config.yaml")

    print(f"[worker] 加载 IndexTTS-2 模型: {model_dir}", flush=True)
    from indextts.infer_v2 import IndexTTS2

    # use_accel: GPT 加速引擎（flash-attn + KV cache + CUDA graph），需 flash-attn
    # use_cuda_kernel: BigVGAN 声码器融合 kernel，需 nvcc+MSVC，失败自动回退
    # use_torch_compile: torch.compile 编译 s2mel 扩散模型，需 triton-windows
    opts = dict(
        use_fp16=bool(job.get("use_fp16", False)),
        use_cuda_kernel=bool(job.get("use_cuda_kernel", False)),
        use_deepspeed=bool(job.get("use_deepspeed", False)),
        use_accel=bool(job.get("use_accel", False)),
        use_torch_compile=bool(job.get("use_torch_compile", False)),
    )
    try:
        tts = IndexTTS2(cfg_path=cfg_path, model_dir=model_dir, **opts)
    except Exception as e:
        # 加速依赖缺失（如 flash-attn / triton 未安装）时自动降级为基础模式
        if opts["use_accel"] or opts["use_torch_compile"] or opts["use_cuda_kernel"]:
            print(f"[worker] 加速模式初始化失败（{e}），自动降级为基础模式重试", flush=True)
            opts["use_accel"] = False
            opts["use_torch_compile"] = False
            opts["use_cuda_kernel"] = False
            tts = IndexTTS2(cfg_path=cfg_path, model_dir=model_dir, **opts)
        else:
            raise
    print("[worker] 模型加载完成，开始合成", flush=True)

    results = []
    items = job["items"]
    for n, item in enumerate(items, 1):
        out = item["output_path"]
        try:
            kwargs = dict(
                spk_audio_prompt=item["ref_audio"],
                text=item["text"],
                output_path=out,
                verbose=False,
            )
            # 情感参考音频（与音色参考一致时即为保留原声情感）
            if item.get("emo_audio"):
                kwargs["emo_audio_prompt"] = item["emo_audio"]
                kwargs["emo_alpha"] = item.get("emo_alpha", 1.0)
            tts.infer(**kwargs)
            ok = os.path.isfile(out) and os.path.getsize(out) > 100
            results.append({"index": item["index"], "ok": ok})
            print(f"[worker] {n}/{len(items)} 片段 {item['index']} "
                  f"{'完成' if ok else '输出为空'}", flush=True)
        except Exception as e:
            traceback.print_exc()
            results.append({"index": item["index"], "ok": False, "error": str(e)})
            print(f"[worker] {n}/{len(items)} 片段 {item['index']} 失败: {e}", flush=True)

    with open(job["result_path"], "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False)
    print(f"[worker] 全部完成: 成功 {sum(1 for r in results if r['ok'])}/{len(results)}", flush=True)


if __name__ == "__main__":
    main()
