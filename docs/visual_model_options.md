# Visual Pen Classifier — Implementation Options

This document covers three approaches to automatically identifying fountain pen brand/model
from images scraped off Yahoo Auctions and Reddit. All are free and can run on a GPU server.

---

## Overview

The pipeline has two distinct sub-problems:

| Sub-problem | What it does |
|---|---|
| **Labeling** | Given a raw image, produce a text label (`"Pilot Custom 742"`) |
| **Classification** | Given a labeled dataset, train a fast inference model |

The three approaches below differ in how much labeled data they require and how complex they
are to set up.

---

## Option 1 — CLIP Zero-Shot (Recommended Starting Point)

**Effort:** Low · **GPU required:** No (GPU just speeds it up) · **Labeled data needed:** None

OpenAI's [CLIP](https://github.com/openai/CLIP) model was trained on 400M image-text pairs and
can match images to arbitrary text prompts with no fine-tuning.

### How it works

For each image, compute its CLIP embedding. Also embed every pen model in the taxonomy as text
(e.g. `"a Pilot Custom 742 fountain pen"`). At inference time, find the text entry with the
highest cosine similarity to the image embedding — that's your predicted model.

### Setup

```bash
pip install transformers torch pillow
```

```python
from transformers import CLIPProcessor, CLIPModel
import torch
from PIL import Image

model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

# Build candidate labels from taxonomy
labels = [
    "a Pilot Custom 742 fountain pen",
    "a Sailor Pro Gear fountain pen",
    "a Pelikan M800 fountain pen",
    # ... one entry per taxonomy row
]

img = Image.open("pen.jpg")
inputs = processor(text=labels, images=img, return_tensors="pt", padding=True)
with torch.no_grad():
    logits = model(**inputs).logits_per_image  # (1, n_labels)
    probs = logits.softmax(dim=1)

best_idx = probs.argmax().item()
confidence = probs[0, best_idx].item()
print(labels[best_idx], f"({confidence:.1%})")
```

### Integration points

- Replace `pen_image_classifier.py`'s `_classify_local()` with CLIP inference.
- Replace `label_images_claude.py` entirely — CLIP can label the manifest directly.
- Text prompts can be tuned: `"photo of a used Pilot Custom 742 fountain pen, blue"` etc.

### Tradeoffs

| Pro | Con |
|---|---|
| Zero labeled data needed | Struggles with very similar-looking models (e.g. Custom 742 vs 743) |
| Works out of the box | Confidence scores are not well-calibrated |
| Taxonomy changes don't require retraining | Needs the taxonomy to have good, descriptive text entries |
| ~150 MB model, CPU-friendly | Less accurate than a fine-tuned model on your specific data |

---

## Option 2 — Open-Source VLM as Claude Replacement (Moondream / LLaVA)

**Effort:** Low-Medium · **GPU required:** Moondream: no; LLaVA: yes (~8 GB VRAM) · **Labeled data needed:** None

Rather than querying Claude's API, run a locally-hosted vision-language model that answers
the same free-text question: *"What fountain pen is this?"*

### Model choices

| Model | Size | VRAM | Notes |
|---|---|---|---|
| [vikhyatk/moondream2](https://huggingface.co/vikhyatk/moondream2) | ~2 GB | CPU-ok | Tiny, fast, surprisingly capable |
| [llava-hf/llava-1.5-7b-hf](https://huggingface.co/llava-hf/llava-1.5-7b-hf) | ~14 GB | ~8 GB | More accurate, better at Japanese text |
| [Qwen/Qwen2-VL-7B-Instruct](https://huggingface.co/Qwen/Qwen2-VL-7B-Instruct) | ~16 GB | ~10 GB | Best at non-English + visual reasoning |

### Setup (Moondream example)

```bash
pip install transformers einops pillow
```

```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from PIL import Image

model = AutoModelForCausalLM.from_pretrained(
    "vikhyatk/moondream2", trust_remote_code=True, device_map="auto"
)
tokenizer = AutoTokenizer.from_pretrained("vikhyatk/moondream2", trust_remote_code=True)

img = Image.open("pen.jpg")
enc = model.encode_image(img)
answer = model.answer_question(enc,
    "Identify this fountain pen. Reply with JSON: "
    '{"brand":"...","line":"...","condition":"...","confidence":"high|medium|low"}',
    tokenizer)
print(answer)
```

### Integration points

- Drop-in replacement for the `_classify_claude()` function in `pen_image_classifier.py`.
- Swap the `anthropic` API call in `label_images_claude.py` for a local model call.
- Output format is identical — still JSON with `brand/line/condition/confidence`.

### Tradeoffs

| Pro | Con |
|---|---|
| No API cost, no rate limits | Moondream occasionally hallucinates pen models |
| Same free-text flexibility as Claude | LLaVA needs a GPU with enough VRAM |
| Can describe condition, finish, nib size | Slower per-image than CLIP (~1–3 s/image on GPU) |
| Works on images Claude would refuse | Output needs JSON parsing with fallback |

---

## Option 3 — Fine-Tuned ViT / EfficientNet with `timm`

**Effort:** Medium · **GPU required:** Yes (~4 GB VRAM minimum) · **Labeled data needed:** ~20–30 images/class

Standard supervised transfer learning: freeze a pretrained ImageNet backbone, replace the
classification head with one sized to your pen taxonomy, fine-tune on labeled images.

### Setup

```bash
pip install timm torch torchvision pillow scikit-learn
```

```python
import timm
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from PIL import Image

# Load pretrained backbone
model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=N_CLASSES)
# or: "vit_small_patch16_224", "resnet50", "convnext_tiny"

optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

# Standard training loop ...
```

The existing `train_pen_classifier.py` script can be updated to use this instead of the
sklearn SVC — the feature extraction step (PIL histogram) would be replaced by the model's
own learned features.

### Data requirements

You need labeled images to fine-tune. Use **Option 1 or 2 first** to auto-label the manifest,
then fine-tune. Minimum viable: 20 images per class, 50+ is comfortable.

Current manifest has 41 images across ~6 brand directories. That is enough to experiment
with CLIP or a VLM, but not yet enough to fine-tune reliably.

### Recommended backbone

`efficientnet_b0` is a good default: small (5.3M params), fast, strong ImageNet baseline,
and pen images tend to be fairly low-res thumbnails where a heavy ViT-L would overfit.

### Tradeoffs

| Pro | Con |
|---|---|
| Highest accuracy once you have enough data | Requires ~20–30 labeled images per class |
| Fast inference after training (~50 ms/image) | Needs retraining when taxonomy grows |
| Well-understood, debuggable | Most engineering effort of the three |
| Confidence scores are well-calibrated | Needs a GPU for training |

---

## Recommended Execution Order

```
Step 1 (now, no GPU needed):
  python3 scripts/collect_pen_images.py --pages 30 --min-confidence low
  → Collect as many Reddit images as possible before moving to GPU server

Step 2 (on GPU server, pick one):
  Option A (fastest): use CLIP to label + classify
    scripts/label_images_clip.py        ← to be written
    scripts/train_pen_classifier.py     ← update to use CLIP embeddings

  Option B (most flexible): use Moondream/LLaVA to label, then CLIP or timm to classify
    scripts/label_images_vlm.py         ← to be written
    scripts/train_pen_classifier.py     ← update for timm fine-tuning

Step 3:
  python3 scripts/train_pen_classifier.py
  → Train and save model to models/visual/

Step 4:
  python3 scripts/scrape_training_data.py --classify-images
  → Auto-label Yahoo auction thumbnails using trained model
```

---

## File Map

| File | Purpose | Status |
|---|---|---|
| `scripts/collect_pen_images.py` | Download images from Reddit | Done |
| `scripts/label_images_claude.py` | Label via Claude Vision API | Done (needs API key) |
| `scripts/label_images_clip.py` | Label via CLIP zero-shot | To be written |
| `scripts/label_images_vlm.py` | Label via Moondream/LLaVA | To be written |
| `scripts/train_pen_classifier.py` | Train sklearn SVC on PIL features | Done (baseline) |
| `apps/api/app/services/pen_image_classifier.py` | Inference service | Done (pluggable) |
| `models/visual/pen_classifier.pkl` | Saved model | Not yet trained |
| `data/images/manifest.jsonl` | Raw image index | 41 entries |
| `data/images/labeled_manifest.jsonl` | Claude-labeled images | Empty (no API key) |
