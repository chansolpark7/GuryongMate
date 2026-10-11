"""multilingual-e5-base 를 ONNX 로 내보내고 (선택) int8 동적 양자화한다. Dockerfile 의 export 단계에서만 돈다.

    python export.py --out /model --quantize emb-int8  # 서버 배포 기본: 단어 임베딩 표만 int8 (약 530MB, 품질 fp32 와 같음)
    python export.py --out /model --quantize int8      # 모든 가중치 int8 (약 280MB, 검색 순위가 눈에 띄게 달라짐)
    python export.py --out /model --quantize fp32      # 양자화 없음 (약 1.1GB)

양자화 방식별 품질(torch 대비 코사인·질의 상위 10 겹침)은 docs/deploy/README.md §1.2.
모델 파라미터의 2/3 가 단어 임베딩 표(250k 단어 x 768)다. 이 표는 int8 로 바꿔도 결과가 거의 같고(코사인 0.9999),
트랜스포머 층(MatMul, 특히 FFN)을 int8 로 바꾸면 코사인 0.97 · 상위 10 겹침 75% 로 떨어진다.

결과: /model/model.onnx, tokenizer.json, meta.json (모델 ID·리비전·양자화·최대 길이)
torch 는 이 단계에만 있고 런타임 이미지에는 들어가지 않는다.
"""
import argparse
import json
import shutil
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from transformers import AutoModel, AutoTokenizer


class Encoder(torch.nn.Module):
    """last_hidden_state 만 내보낸다 (평균 풀링·정규화는 서버가 numpy 로)."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, input_ids, attention_mask):
        return self.model(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="intfloat/multilingual-e5-base")
    ap.add_argument("--revision", default="d128750597153bb5987e10b1c3493a34e5a4502a")
    ap.add_argument("--out", default="/model")
    ap.add_argument("--quantize", choices=["emb-int8", "int8", "fp32"], default="emb-int8")
    a = ap.parse_args()

    src = Path(snapshot_download(a.model, revision=a.revision, allow_patterns=[
        "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json",
        "special_tokens_map.json", "sentencepiece.bpe.model", "sentence_bert_config.json"]))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    max_len = json.loads((src / "sentence_bert_config.json").read_text())["max_seq_length"]

    model = AutoModel.from_pretrained(src).eval()
    tok = AutoTokenizer.from_pretrained(src)
    sample = tok(["query: 구룡포 과메기", "passage: 일본인 가옥거리는 1920년대 거리다"], padding=True, return_tensors="pt")
    fp32 = out / "model_fp32.onnx"
    with torch.no_grad():
        torch.onnx.export(
            Encoder(model), (sample["input_ids"], sample["attention_mask"]), str(fp32),
            input_names=["input_ids", "attention_mask"], output_names=["last_hidden_state"],
            dynamic_axes={"input_ids": {0: "batch", 1: "seq"}, "attention_mask": {0: "batch", 1: "seq"},
                          "last_hidden_state": {0: "batch", 1: "seq"}},
            opset_version=17, do_constant_folding=True)

    if a.quantize != "fp32":
        from onnxruntime.quantization import QuantType, quantize_dynamic
        # 가중치만 int8 (활성값은 실행 중 동적으로). emb-int8 은 단어 임베딩 표(Gather)만
        ops = ["Gather"] if a.quantize == "emb-int8" else None
        quantize_dynamic(str(fp32), str(out / "model.onnx"), weight_type=QuantType.QInt8, per_channel=True,
                         op_types_to_quantize=ops)
        fp32.unlink()
    else:
        fp32.rename(out / "model.onnx")

    shutil.copy(src / "tokenizer.json", out / "tokenizer.json")
    (out / "meta.json").write_text(json.dumps({
        "model_id": a.model, "revision": a.revision, "quantize": a.quantize, "max_seq_length": max_len,
        "dim": model.config.hidden_size, "pad_token_id": tok.pad_token_id, "pad_token": tok.pad_token}, indent=1))
    print(f"exported {a.model} ({a.quantize}) -> {out}: "
          + ", ".join(f"{p.name} {p.stat().st_size / 1e6:.0f}MB" for p in sorted(out.iterdir())))


if __name__ == "__main__":
    main()
