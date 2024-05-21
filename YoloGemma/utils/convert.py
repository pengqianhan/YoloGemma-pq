# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.
import json
import re
import sys
from pathlib import Path
from typing import Optional

import torch

# support running without installing as a package
wd = Path(__file__).parent.parent.resolve()
sys.path.append(str(wd))

from YoloGemma.model import ModelArgs


@torch.inference_mode()
def convert_hf_checkpoint(
    *,
    checkpoint_dir: str = "checkpoints/meta-Transformer/Transformer-2-7b-chat-hf",
    model_name: Optional[str] = None,
) -> None:
    checkpoint_dir = Path(checkpoint_dir)  # Ensure checkpoint_dir is a Path object
    if model_name is None:
        model_name = checkpoint_dir.name

    config = ModelArgs.from_name(model_name)
    print(f"Model config {config.__dict__}")

    from safetensors import safe_open

    # Load the json file containing weight mapping
    model_map_json = checkpoint_dir / "model.safetensors.index.json"

    assert model_map_json.is_file()

    with open(model_map_json) as json_map:
        bin_index = json.load(json_map)

    

    weight_map = {
        "language_model.model.embed_tokens.weight": "tok_embeddings.weight",
        "language_model.model.layers.{}.self_attn.q_proj.weight": "layers.{}.attention.wq.weight",
        "language_model.model.layers.{}.self_attn.k_proj.weight": "layers.{}.attention.wk.weight",
        "language_model.model.layers.{}.self_attn.v_proj.weight": "layers.{}.attention.wv.weight",
        "language_model.model.layers.{}.self_attn.o_proj.weight": "layers.{}.attention.wo.weight",
        'language_model.model.layers.{}.self_attn.rotary_emb.inv_freq': None,
        'language_model.model.layers.{}.mlp.gate_proj.weight': 'layers.{}.feed_forward.w1.weight',
        "language_model.model.layers.{}.mlp.up_proj.weight": "layers.{}.feed_forward.w3.weight",
        "language_model.model.layers.{}.mlp.down_proj.weight": "layers.{}.feed_forward.w2.weight",
        "language_model.model.layers.{}.input_layernorm.weight": "layers.{}.attention_norm.weight",
        "language_model.model.layers.{}.post_attention_layernorm.weight": "layers.{}.ffn_norm.weight",
        "language_model.model.norm.weight": "norm.weight",
    }
    bin_files = {checkpoint_dir / bin for bin in bin_index["weight_map"].values()}

    def permute(w, n_head):
        dim = config.dim
        # print(config.head_dim)
        # print(config.dim)
        return (
            w.view(n_head, 2, config.head_dim // 2, dim)
            .transpose(1, 2)
            .reshape(config.head_dim * n_head, dim)
        )

    merged_result = {}
    for file in sorted(bin_files):
        state_dict = safe_open(str(file), framework="pt", device='cpu')
        state_dict = {k: state_dict.get_tensor(k) for k in state_dict.keys()}
        merged_result.update(state_dict)
    final_result = {}
    for key, value in merged_result.items():
        if 'language_model' in key:
            if "layers" in key:
                abstract_key = re.sub(r'(\d+)', '{}', key)
                layer_num = re.search(r'\d+', key).group(0)
                new_key = weight_map[abstract_key]
                if new_key is None:
                    continue
                new_key = new_key.format(layer_num)
            else:
                new_key = weight_map[key]
    
            final_result[new_key] = value

    for key in tuple(final_result.keys()):
        if "wq" in key:
            q = final_result[key]
            k = final_result[key.replace("wq", "wk")]
            v = final_result[key.replace("wq", "wv")]
            q = permute(q, config.n_head)
            k = permute(k, config.n_local_heads)
            final_result[key.replace("wq", "wqkv")] = torch.cat([q, k, v])
            del final_result[key]
            del final_result[key.replace("wq", "wk")]
            del final_result[key.replace("wq", "wv")]
    if "output.weight" not in final_result:
        final_result["output.weight"] = final_result["tok_embeddings.weight"]


    print(f"Saving checkpoint to {checkpoint_dir / 'model.pth'}")
    torch.save(final_result, checkpoint_dir / "model.pth")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Convert HuggingFace checkpoint.')
    parser.add_argument('--checkpoint_dir', type=Path, default=Path("checkpoints/meta-llama/llama-2-7b-chat-hf"))
    parser.add_argument('--model_name', type=str, default=None)

    args = parser.parse_args()
    convert_hf_checkpoint(
        checkpoint_dir=args.checkpoint_dir,
        model_name=args.model_name,
    )
